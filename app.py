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
# Streamlit CloudのSecrets、またはローカルのsecrets.tomlから読み込む
GITHUB_TOKEN = st.secrets.get("github", {}).get("token")
REPO_NAME = st.secrets.get("github", {}).get("repo") # "username/repo"
BRANCH = st.secrets.get("github", {}).get("branch", "main")

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
                # GitHub上のファイル内容（Base64）をデコード
                content = base64.b64decode(response.json()['content']).decode('utf-8')
                data = json.loads(content)
            elif response.status_code == 404:
                # ファイルがまだない場合は空リスト
                data = []
            # else: エラー時はローカルへフォールバック
        except Exception:
            pass # GitHub失敗時はローカルを試す
    
    # 2. GitHub設定がない、または失敗した場合はローカルファイルを確認
    if not data and os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []
    
    # 既存データ補完（必須キーがない場合のフォールバック）
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
    
    # 1. ローカル保存（一時的なキャッシュとして）
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        f.write(json_str)

    # 2. GitHubへアップロード（設定がある場合）
    if GITHUB_TOKEN and REPO_NAME:
        url = f"https://api.github.com/repos/{REPO_NAME}/contents/{DATA_FILE}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
        
        # まず現在のファイルのSHAハッシュを取得（上書きに必要）
        sha = None
        try:
            get_resp = requests.get(url + f"?ref={BRANCH}", headers=headers)
            if get_resp.status_code == 200:
                sha = get_resp.json()['sha']
        except:
            pass

        # 更新リクエストの作成
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
            # エラーハンドリングは簡易化
        except Exception as e:
            st.error(f"GitHub保存例外: {e}")

def normalize_title(title):
    """タイトルから巻数や補足情報を強力に除去してシリーズ名を抽出する"""
    if not title: return ""
    
    # 1. NFKC正規化
    title = unicodedata.normalize('NFKC', title)
    
    # 2. 具体的な巻数パターンの削除
    patterns = [
        r'\s*\(\d+\)', r'\s*\[\d+\]', r'\s*<\d+>', 
        r'\s*第\d+巻', r'\s*第\d+集', r'\s*\d+巻',
        r'\s*Vol\.?\s*\d+', r'\s*Volume\.?\s*\d+', r'\s*#\d+',
    ]
    for pattern in patterns:
        title = re.sub(pattern, ' ', title, flags=re.IGNORECASE)

    # 3. 独立した数字の削除
    title = re.sub(r'\s+\d+(\s|$)', ' ', title)
    
    # 4. 余分なスペースを整理
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

def search_rakuten_books(query, app_id, genre_id="001001", hits=30):
    if not query or not app_id: return []
    
    registered_isbns = set()
    if 'manga_data' in st.session_state:
        for d in st.session_state.manga_data:
            if d.get('isbn'): registered_isbns.add(d['isbn'])

    url = "https://app.rakuten.co.jp/services/api/BooksTotal/Search/20170404"
    params = {"applicationId": app_id, "keyword": query, "hits": hits, "sort": "+releaseDate"}
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
                if isbn and isbn in registered_isbns: continue
                if title and not any(r['title'] == title for r in results):
                    results.append({
                        "title": title, "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""), "image": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""), "isbn": isbn, "releaseDate": info.get("salesDate", ""),
                        "source": "Rakuten"
                    })
        return results
    except: return []

def get_next_volume_info(series_title, next_vol, app_id):
    if not app_id: return None
    query = f"{series_title} {next_vol}"
    # 通常版優先ロジック
    results = search_rakuten_books(query, app_id, genre_id="001001", hits=10)
    if not results: return None
    
    exclude_keywords = ["特装版", "限定版", "同梱版", "プレミアム", "ドラマCD", "小冊子", "付録"]
    for res in results:
        if not any(kw in res["title"] for kw in exclude_keywords): return res
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

# --- サイドバー ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio("表示モード", ["➕ 漫画登録＆ライブラリ", "🏆 全件リスト", "🆕 新着ビュー", "🔖 未読・欲しい", "💎 完結＆高評価", "🎨 ジャンル別"])
    st.divider()
    
    st.header("⚙️ 設定")
    rakuten_app_id = st.text_input("楽天 Application ID", type="password")
    
    # GitHub連携状態の表示
    if GITHUB_TOKEN and REPO_NAME:
        st.success(f"☁️ GitHub連携中")
    else:
        st.info("☁️ GitHub未設定 (一時保存モード)")

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

    with st.container():
        search_query = st.text_input("タイトル検索 (入力してEnterで候補表示)", placeholder="例: 呪術廻戦", key="s_in")
        filter_option = st.radio("検索ジャンル:", ["漫画", "書籍", "アニメ", "ゲーム", "すべて"], index=0, horizontal=True)
        genre_map = {"漫画":"001001", "書籍":"001", "アニメ":"003", "ゲーム":"006", "すべて":""}
        genre_id = genre_map.get(filter_option, "")

        if search_query and rakuten_app_id and search_query != st.session_state.last_search_query:
            with st.spinner('候補を検索中...'):
                st.session_state.selected_book = None
                results = search_rakuten_books(search_query, rakuten_app_id, genre_id)
                st.session_state.search_results = results
                st.session_state.last_search_query = search_query 
                if not results: st.warning("見つかりませんでした。")

        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"{r['title']} - {r['author']}" for r in st.session_state.search_results]
            sel = st.selectbox("↓ 候補から選択", opts, key="s_sel")
            if sel != "(選択してください)":
                st.session_state.selected_book = st.session_state.search_results[opts.index(sel)-1]

    init = {"title":"", "image":"", "author":"", "publisher":"", "isbn":"", "link":"", "volume": 1}
    if st.session_state.selected_book: 
        init = st.session_state.selected_book.copy()
        init["volume"] = extract_volume(init["title"])
        init["title"] = normalize_title(init["title"])

    with st.form("reg"):
        c1, c2 = st.columns([2, 1])
        with c1:
            st.caption("※シリーズ名として登録されます")
            title = st.text_input("タイトル (シリーズ名)", init["title"])
            r1, r2, r3 = st.columns(3)
            vol = r1.number_input("巻数", 1, step=1, value=init["volume"])
            status = r2.selectbox("状態", ["own", "want"])
            score = r3.slider("評価", 0, 5, 3)
            r4, r5 = st.columns(2)
            genre = r4.text_input("ジャンル", placeholder="少年, アクション")
            date = r5.text_input("次巻発売日", placeholder="YYYY年MM月DD日", value=init.get("releaseDate", ""))
            r6, r7 = st.columns(2)
            f_chk = r6.checkbox("完結済み")
            u_chk = r7.checkbox("未読")
        with c2:
            if init.get("image"): st.image(init["image"], width=100)
            else: st.info("No Image")

        if st.form_submit_button("追加") and title:
            new_d = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": title, "volume": vol, "releaseDate": date, "status": status,
                "my_score": score, "genre": genre, "is_finished": f_chk, "is_unread": u_chk,
                "image": init.get("image", ""), "author": init.get("author", ""),
                "publisher": init.get("publisher", ""), "isbn": init.get("isbn", ""), "link": init.get("link", "")
            }
            st.session_state.manga_data.append(new_d)
            save_data(st.session_state.manga_data)
            st.success(f"『{title}』 Vol.{vol} を追加しました")
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.session_state.last_search_query = "" 
            st.rerun()

    st.divider()
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
                        with st.spinner("検索中... (通常版を優先)"):
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
