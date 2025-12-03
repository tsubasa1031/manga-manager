import streamlit as st
import pandas as pd
import json
import os
import requests
import re
import unicodedata
import base64
from datetime import datetime

# --- 設定 ---
DATA_FILE = 'manga_data.json'

# --- GitHub設定の読み込み ---
GITHUB_TOKEN = st.secrets.get("github", {}).get("token")
REPO_NAME = st.secrets.get("github", {}).get("repo") 
BRANCH = st.secrets.get("github", {}).get("branch", "main")

# --- 楽天設定の読み込み ---
RAKUTEN_APP_ID_SECRET = st.secrets.get("rakuten", {}).get("app_id", "")

# --- 関数定義 ---

def load_data():
    """データを読み込む（GitHub優先、設定なければローカル）"""
    data = []
    
    # 1. GitHubからロードを試みる
    if GITHUB_TOKEN and REPO_NAME:
        url = f"https://api.github.com/repos/{REPO_NAME}/contents/{DATA_FILE}?ref={BRANCH}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                content = base64.b64decode(response.json()['content']).decode('utf-8')
                data = json.loads(content)
            elif response.status_code == 404:
                data = []
        except Exception:
            pass 
    
    # 2. GitHub設定がない、または失敗した場合はローカルファイルを確認
    if not data and os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []
    
    # 既存データ補完
    for d in data:
        d.setdefault('my_score', 0)
        d.setdefault('genre', '未分類')
        d.setdefault('is_finished', False)
        d.setdefault('is_unread', False)
        d.setdefault('title', 'No Title')
        d.setdefault('status', 'want')
    return data

def save_data(data):
    """データを保存する（GitHubがあればアップロード、ローカルも更新）"""
    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        f.write(json_str)

    if GITHUB_TOKEN and REPO_NAME:
        url = f"https://api.github.com/repos/{REPO_NAME}/contents/{DATA_FILE}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        
        sha = None
        try:
            get_resp = requests.get(url + f"?ref={BRANCH}", headers=headers)
            if get_resp.status_code == 200:
                sha = get_resp.json()['sha']
        except:
            pass

        content_b64 = base64.b64encode(json_str.encode('utf-8')).decode('utf-8')
        payload = {
            "message": f"Update data {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "content": content_b64,
            "branch": BRANCH
        }
        if sha:
            payload["sha"] = sha
            
        try:
            requests.put(url, headers=headers, json=payload)
        except Exception as e:
            st.error(f"GitHub保存例外: {e}")

def normalize_title(title):
    """タイトルから巻数や補足情報を強力に除去してシリーズ名を抽出する"""
    if not title: return ""
    title = unicodedata.normalize('NFKC', title)
    patterns = [
        r'\s*\(\d+\)', r'\s*\[\d+\]', r'\s*<\d+>', 
        r'\s*第\d+巻', r'\s*第\d+集', r'\s*\d+巻',
        r'\s*Vol\.?\s*\d+', r'\s*Volume\.?\s*\d+', r'\s*#\d+',
    ]
    for pattern in patterns:
        title = re.sub(pattern, ' ', title, flags=re.IGNORECASE)
    title = re.sub(r'\s+\d+(\s|$)', ' ', title)
    return re.sub(r'\s+', ' ', title).strip()

def extract_volume(title):
    """タイトルから巻数を抽出する"""
    if not title: return 1
    title_norm = unicodedata.normalize('NFKC', title)
    patterns = [
        r'第(\d+)巻', r'\d+巻', r'Vol\.?(\d+)', 
        r'[\(\[\<](\d+)[\)\]\>]', r'\s(\d+)\s', r'(\d+)$',
    ]
    for pattern in patterns:
        match = re.search(pattern, title_norm, re.IGNORECASE)
        if match: return int(match.group(1))
    return 1

# --- 楽天ブックスAPI 関連関数 ---

def search_rakuten_books(query, app_id, genre_id="001001", hits=30, sort="+releaseDate"):
    if not query or not app_id: return []
    
    # 重複チェック用（既存データ）
    registered_isbns = set()
    if 'manga_data' in st.session_state:
        for d in st.session_state.manga_data:
            if d.get('isbn'): registered_isbns.add(d['isbn'])

    url = "https://app.rakuten.co.jp/services/api/BooksTotal/Search/20170404"
    params = {"applicationId": app_id, "keyword": query, "hits": hits, "sort": sort}
    if genre_id: params["booksGenreId"] = genre_id

    results = []
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if "Items" in data:
            for item in data["Items"]:
                info = item.get("Item", {})
                title = info.get("title", "")
                isbn = info.get("isbn", "")
                
                # 検索結果には登録済みも含める（最新刊チェックなどのため）が、
                # リスト表示時に除外するかは用途による。ここではそのまま返す。
                
                if title and not any(r['title'] == title for r in results):
                    results.append({
                        "title": title, "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""), "image": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""), "isbn": isbn, "releaseDate": info.get("salesDate", ""),
                        "source": "Rakuten"
                    })
        return results
    except: return []

def get_series_stats(series_title, app_id):
    """
    シリーズの最新刊数と、1巻の情報を取得する
    Return: (max_volume, first_vol_info_dict)
    """
    if not app_id or not series_title: return 1, None
    
    # 1. 最新刊を探す (新しい順)
    latest_results = search_rakuten_books(series_title, app_id, hits=5, sort="-releaseDate")
    max_vol = 1
    exclude_keywords = ["特装版", "限定版", "同梱版", "小冊子"]
    
    if latest_results:
        for res in latest_results:
            # 特装版などは数字が変な場合があるのでなるべく避けるが、数字が取れれば採用
            vol = extract_volume(res['title'])
            if vol > max_vol:
                max_vol = vol
                
    # 2. 1巻を探す (古い順) - シリーズ画像などのメタデータ用
    first_results = search_rakuten_books(series_title, app_id, hits=1, sort="+releaseDate")
    first_vol_info = first_results[0] if first_results else None
    
    return max_vol, first_vol_info

def get_next_volume_info(series_title, next_vol, app_id):
    """次巻情報取得 (通常版優先)"""
    if not app_id: return None
    query = f"{series_title} {next_vol}"
    results = search_rakuten_books(query, app_id, hits=10, sort="+releaseDate") # 関連度順の方がいいかもだが
    if not results: return None
    exclude = ["特装版", "限定版", "同梱版", "ドラマCD"]
    for res in results:
        if not any(kw in res["title"] for kw in exclude): return res
    return results[0]


# --- アプリケーション本体 ---

st.set_page_config(page_title="漫画管理アプリ", layout="wide")

if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_book' not in st.session_state:
    st.session_state.selected_book = None
if 'last_search_query' not in st.session_state:
    st.session_state.last_search_query = ""
# シリーズ一括登録用の状態
if 'series_max_vol' not in st.session_state:
    st.session_state.series_max_vol = 1
if 'series_meta_info' not in st.session_state:
    st.session_state.series_meta_info = {}

# --- サイドバー ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio("表示モード", ["➕ 漫画登録＆ライブラリ", "🏆 全件リスト", "🆕 新着ビュー", "🔖 未読・欲しい", "💎 完結＆高評価", "🎨 ジャンル別"])
    st.divider()
    
    st.header("⚙️ 設定")
    rakuten_app_id = st.text_input("楽天 Application ID", value=RAKUTEN_APP_ID_SECRET, type="password")
    
    if GITHUB_TOKEN and REPO_NAME:
        st.success(f"☁️ GitHub連携中")
    else:
        st.info("☁️ GitHub未設定")

# --- 共通関数 ---
def update_data(edited_df):
    updated_list = edited_df.to_dict(orient="records")
    current_data_map = {d['id']: d for d in st.session_state.manga_data}
    for item in updated_list:
        if item['id'] in current_data_map:
            current_data_map[item['id']] = item
    st.session_state.manga_data = list(current_data_map.values())
    save_data(st.session_state.manga_data)

@st.dialog("詳細編集")
def edit_dialog(item):
    with st.form(f"edit_form_{item['id']}"):
        col1, col2 = st.columns([1, 2])
        with col1:
            if item.get("image"): st.image(item["image"], width=100)
            else: st.write("No Image")
        with col2:
            new_title = st.text_input("タイトル", item["title"])
            new_vol = st.number_input("巻数", value=item["volume"], step=1)
            new_status = st.selectbox("状態", ["own", "want"], index=0 if item["status"]=="own" else 1)
            new_score = st.slider("評価", 0, 5, item["my_score"])
            new_date = st.text_input("発売日", item["releaseDate"])
            new_unread = st.checkbox("未読", item["is_unread"])
            
            if st.form_submit_button("更新"):
                for d in st.session_state.manga_data:
                    if d['id'] == item['id']:
                        d['title'] = new_title; d['volume'] = new_vol; d['status'] = new_status
                        d['my_score'] = new_score; d['releaseDate'] = new_date; d['is_unread'] = new_unread
                        break
                save_data(st.session_state.manga_data)
                st.rerun()
            
            if st.form_submit_button("削除", type="primary"):
                st.session_state.manga_data = [d for d in st.session_state.manga_data if d['id'] != item['id']]
                save_data(st.session_state.manga_data)
                st.rerun()

# --- メインビュー ---
if view_mode == "➕ 漫画登録＆ライブラリ":
    st.header("漫画登録")
    if not rakuten_app_id: st.warning("⚠️ サイドバーで楽天Application IDを設定してください。")

    # --- 1. 検索＆シリーズ選択エリア ---
    with st.container():
        search_query = st.text_input("タイトル検索 (入力してEnter)", placeholder="例: 呪術廻戦", key="s_in")
        
        # 自動検索ロジック
        if search_query and rakuten_app_id and search_query != st.session_state.last_search_query:
            with st.spinner('候補を検索中...'):
                st.session_state.selected_book = None
                # ジャンルは漫画固定で検索
                results = search_rakuten_books(search_query, rakuten_app_id, genre_id="001001", hits=20)
                st.session_state.search_results = results
                st.session_state.last_search_query = search_query 
                st.session_state.series_max_vol = 1 # リセット
                if not results: st.warning("見つかりませんでした。")

        # 候補リスト選択
        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"{r['title']} - {r['author']}" for r in st.session_state.search_results]
            sel = st.selectbox("↓ シリーズを選択してください", opts, key="s_sel")
            
            # 選択が変更されたら最新刊情報を取得
            if sel != "(選択してください)":
                current_sel = st.session_state.search_results[opts.index(sel)-1]
                # 前回選択と同じならスキップ（API節約）
                if st.session_state.selected_book != current_sel:
                    st.session_state.selected_book = current_sel
                    # 正規化タイトルで最新刊数を検索
                    norm_title = normalize_title(current_sel['title'])
                    with st.spinner(f'「{norm_title}」の最新刊情報を確認中...'):
                        max_vol, meta_info = get_series_stats(norm_title, rakuten_app_id)
                        st.session_state.series_max_vol = max_vol
                        st.session_state.series_meta_info = meta_info if meta_info else current_sel

    # --- 2. 一括登録フォーム ---
    if st.session_state.selected_book:
        series_title = normalize_title(st.session_state.selected_book['title'])
        max_v = st.session_state.series_max_vol
        meta = st.session_state.series_meta_info
        
        st.markdown(f"### 📖 {series_title}")
        st.caption(f"最新刊はおそらく **{max_v}巻** です")

        with st.form("bulk_reg"):
            st.info("所持している巻数をスライダーで指定してください。")
            
            # スライダー (1巻〜最新刊)
            # max_vが1の場合はスライダーの意味がないので最小2にする
            slider_max = max(max_v, 2)
            owned_vol = st.slider("何巻まで持っていますか？", 1, slider_max, 1)
            
            st.write(f"👉 **1巻 〜 {owned_vol}巻** を「持ってる」状態で登録します")
            
            c1, c2 = st.columns(2)
            with c1:
                genre = st.text_input("ジャンル", placeholder="少年, アクション", value="少年")
            with c2:
                is_unread = st.checkbox("未読として登録する", value=False)

            if st.form_submit_button("一括登録する"):
                added_count = 0
                # 1巻から指定巻数までループ
                for v in range(1, owned_vol + 1):
                    # 既に登録済みかチェック（ID重複はしないが、タイトル+巻数でチェックしたい）
                    # 簡易的に既存リストを走査
                    exists = False
                    for existing in st.session_state.manga_data:
                        if existing['title'] == series_title and existing['volume'] == v:
                            exists = True
                            break
                    
                    if not exists:
                        # 1巻の画像などをシリーズ共通画像として使用
                        # (個別の表紙は後で詳細編集で直す運用)
                        img_url = meta.get("image", "") if meta else ""
                        link_url = meta.get("link", "") if meta else ""
                        author_name = meta.get("author", "") if meta else ""
                        pub_name = meta.get("publisher", "") if meta else ""
                        
                        new_d = {
                            "id": datetime.now().strftime("%Y%m%d%H%M%S") + str(v), # IDユニーク化
                            "title": series_title,
                            "volume": v,
                            "releaseDate": "", # 一括なので日付は空にしておく（必要ならAPI叩くが遅くなる）
                            "status": "own",   # 所持
                            "my_score": 0,
                            "genre": genre,
                            "is_finished": False,
                            "is_unread": is_unread,
                            "image": img_url,
                            "author": author_name,
                            "publisher": pub_name,
                            "isbn": "", 
                            "link": link_url
                        }
                        st.session_state.manga_data.append(new_d)
                        added_count += 1
                
                save_data(st.session_state.manga_data)
                if added_count > 0:
                    st.success(f"『{series_title}』を1〜{owned_vol}巻まで登録しました！（計{added_count}冊）")
                    # リセット
                    st.session_state.search_results = []
                    st.session_state.selected_book = None
                    st.session_state.last_search_query = ""
                    st.rerun()
                else:
                    st.warning("指定された巻はすべて登録済みです。")

    st.divider()
    
    # --- 3. 本棚 (シリーズ別) ---
    st.subheader("📚 本棚")

    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df['series_key'] = df['title'].apply(normalize_title)
        
        series_groups = []
        for key, group in df.groupby('series_key'):
            min_vol_row = group.loc[group['volume'].idxmin()]
            latest_row = group.loc[group['volume'].idxmax()]
            
            series_groups.append({
                "title": key if key else "No Title",
                "df": group.sort_values("volume"),
                "image": min_vol_row.get('image', ''),
                "link": min_vol_row.get('link', ''),
                "last_updated": group['id'].max(),
                "max_vol": group['volume'].max(),
                "meta": latest_row.to_dict()
            })
        
        series_groups.sort(key=lambda x: x['last_updated'], reverse=True)
        cols = st.columns(4)

        for i, series in enumerate(series_groups):
            col = cols[i % 4]
            with col:
                if series['image']:
                    link_target = series['link'] if series['link'] else "#"
                    st.markdown(f"[![{series['title']}]({series['image']})]({link_target})", unsafe_allow_html=True)
                else:
                    st.markdown(f"<div style='background:#eee;height:150px;text-align:center;padding:60px 0;'>No Img</div>", unsafe_allow_html=True)
                
                with st.expander(f"📂 {series['title']} ({len(series['df'])})"):
                    next_vol_num = int(series['max_vol']) + 1
                    if st.button(f"➕ Vol.{next_vol_num} 追加", key=f"add_n_{series['title']}"):
                        with st.spinner("検索中..."):
                            new_info = get_next_volume_info(series['title'], next_vol_num, rakuten_app_id)
                            base = series['meta']
                            new_entry = {
                                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                                "title": series['title'], "volume": next_vol_num, "status": "want",
                                "my_score": 0, "genre": base.get("genre", ""), "is_finished": False, "is_unread": True,
                                "author": base.get("author", ""), "publisher": base.get("publisher", ""),
                                "image": new_info.get("image", "") if new_info else "",
                                "link": new_info.get("link", "") if new_info else "",
                                "isbn": new_info.get("isbn", "") if new_info else "",
                                "releaseDate": new_info.get("releaseDate", "") if new_info else ""
                            }
                            st.session_state.manga_data.append(new_entry)
                            save_data(st.session_state.manga_data)
                            st.toast(f"Vol.{next_vol_num} 追加！")
                            st.rerun()
                    st.divider()
                    
                    vol_cols = st.columns(4)
                    for j, (idx, row) in enumerate(series['df'].iterrows()):
                        with vol_cols[j % 4]:
                            if row.get("image"): st.image(row["image"], use_container_width=True)
                            else: st.caption("No Image")
                            if st.button("編集", key=f"ve_{row['id']}"):
                                edit_dialog(row.to_dict())
    else:
        st.info("まだ漫画が登録されていません。")

# --- 他のビュー (表定義など) ---
common_column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル", "volume": st.column_config.NumberColumn("巻", format="%d"),
    "releaseDate": st.column_config.TextColumn("発売日"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"]),
    "my_score": st.column_config.NumberColumn("評価", format="%d⭐"),
    "is_finished": st.column_config.CheckboxColumn("完"),
    "is_unread": st.column_config.CheckboxColumn("未読"),
    "link": st.column_config.LinkColumn("Link"),
    "id": None, "author": None, "publisher": None, "isbn": None, "genre": None
}

if view_mode == "🏆 全件リスト":
    st.header("🏆 全件リスト")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data).sort_values(["my_score", "title"], ascending=[False, True])
        e_df = st.data_editor(df, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_all")
        if not df.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "🆕 新着ビュー":
    st.header("🆕 新着ビュー")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data).sort_values("id", ascending=False)
        e_df = st.data_editor(df, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_new")
        if not df.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "🔖 未読・欲しい":
    st.header("🔖 未読・欲しい")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df_u = df[(df['status']=='want')|(df['is_unread']==True)].sort_values("releaseDate", ascending=False)
        if not df_u.empty:
            e_df = st.data_editor(df_u, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_un")
            if not df_u.equals(e_df): update_data(e_df); st.rerun()
        else: st.success("なし")

if view_mode == "💎 完結＆高評価":
    st.header("💎 完結＆高評価")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df_m = df[(df['is_finished']==True)&(df['my_score']>=4)].sort_values("my_score", ascending=False)
        if not df_m.empty:
            e_df = st.data_editor(df_m, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_mst")
            if not df_m.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "🎨 ジャンル別":
    st.header("🎨 ジャンル別")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        genres = set()
        for g in df['genre'].unique():
            if g:
                for sub in g.replace('、',',').split(','): genres.add(sub.strip())
        if "" in genres: genres.remove("")
        g_list = sorted(list(genres)) + ["未分類"]
        for g in g_list:
            mask = (df['genre']=="")|(df['genre']=="未分類") if g=="未分類" else df['genre'].str.contains(g, na=False)
            df_g = df[mask].sort_values("my_score", ascending=False)
            if not df_g.empty:
                with st.expander(f"{g} ({len(df_g)})", expanded=True):
                    st.dataframe(df_g, column_config=common_column_config, use_container_width=True, hide_index=True)

st.divider()
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
    st.download_button("CSV保存", df.to_csv(index=False).encode('utf-8-sig'), "manga.csv", "text/csv")
