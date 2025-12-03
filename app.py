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

# --- Secrets読み込み ---
# GitHub
GITHUB_TOKEN = st.secrets.get("github", {}).get("token")
REPO_NAME = st.secrets.get("github", {}).get("repo") 
BRANCH = st.secrets.get("github", {}).get("branch", "main")

# 楽天 (入れ子になっていない場合や、キー名の間違いに対応するため安全に取得)
# [rakuten] app_id = "..." の形式を想定
RAKUTEN_APP_ID_SECRET = ""
if "rakuten" in st.secrets:
    RAKUTEN_APP_ID_SECRET = st.secrets["rakuten"].get("app_id", "")
elif "RAKUTEN_APP_ID" in st.secrets:
    RAKUTEN_APP_ID_SECRET = st.secrets["RAKUTEN_APP_ID"]

# --- 関数定義 ---

def load_data():
    """データを読み込む"""
    data = []
    # 1. GitHubからロード
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
    
    # 2. ローカルからロード
    if not data and os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []
    
    # データ補完（is_unreadは削除）
    for d in data:
        d.setdefault('my_score', 0)
        d.setdefault('genre', '未分類')
        d.setdefault('is_finished', False)
        d.setdefault('title', 'No Title')
        d.setdefault('status', 'own') # 基本的に所持のみ
    return data

def save_data(data):
    """データを保存する"""
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
    """シリーズ名抽出（強力版）"""
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
    """巻数抽出"""
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

# --- 楽天ブックスAPI ---

def search_rakuten_books(query, app_id, genre_id="001001", hits=30, sort="+releaseDate"):
    if not query or not app_id: return []
    
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
                
                # 重複除外なしで検索結果を返す（最新刊判定のため）
                if title:
                    results.append({
                        "title": title, "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""), "image": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""), "isbn": isbn, "releaseDate": info.get("salesDate", ""),
                        "source": "Rakuten"
                    })
        return results
    except: return []

def get_series_stats(series_title, app_id):
    """シリーズの最新刊（最大巻数）と代表情報を取得"""
    if not app_id or not series_title: return 1, None
    
    # 新しい順に検索して最大巻数を探す
    results = search_rakuten_books(series_title, app_id, hits=20, sort="-releaseDate")
    max_vol = 1
    meta = None
    
    if results:
        meta = results[0] # 最新のものをメタデータ候補にする
        for res in results:
            v = extract_volume(res['title'])
            if v > max_vol:
                max_vol = v
                
        # 1巻の画像が欲しいので古い順でも検索してみる
        old_results = search_rakuten_books(series_title, app_id, hits=1, sort="+releaseDate")
        if old_results:
            meta = old_results[0] # 1巻の情報を優先

    return max_vol, meta

def get_next_volume_info(series_title, next_vol, app_id):
    """次巻情報取得"""
    if not app_id: return None
    query = f"{series_title} {next_vol}"
    results = search_rakuten_books(query, app_id, hits=10, sort="+releaseDate")
    if not results: return None
    exclude = ["特装版", "限定版", "同梱版"]
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
# シリーズ情報保持用
if 'series_max_vol' not in st.session_state:
    st.session_state.series_max_vol = 1
if 'series_meta_info' not in st.session_state:
    st.session_state.series_meta_info = {}

# --- サイドバー ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio("表示モード", ["➕ 漫画登録", "🏆 全件リスト", "🎨 ジャンル別"])
    st.divider()
    
    st.header("⚙️ 設定")
    # Secretsから取得した値をデフォルト値に設定
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
            new_score = st.slider("評価", 0, 5, item["my_score"])
            new_date = st.text_input("発売日", item["releaseDate"])
            # ステータス編集は残すが、基本own
            
            if st.form_submit_button("更新"):
                for d in st.session_state.manga_data:
                    if d['id'] == item['id']:
                        d['title'] = new_title; d['volume'] = new_vol
                        d['my_score'] = new_score; d['releaseDate'] = new_date
                        break
                save_data(st.session_state.manga_data)
                st.rerun()
            
            if st.form_submit_button("削除", type="primary"):
                st.session_state.manga_data = [d for d in st.session_state.manga_data if d['id'] != item['id']]
                save_data(st.session_state.manga_data)
                st.rerun()

# --- メインビュー ---
if view_mode == "➕ 漫画登録":
    st.header("漫画登録 (所持コミック)")
    if not rakuten_app_id: st.warning("⚠️ サイドバーで楽天Application IDを設定してください。")

    # 1. 検索エリア
    with st.container():
        search_query = st.text_input("タイトル検索 (入力してEnter)", placeholder="例: 呪術廻戦", key="s_in")
        
        # 自動検索
        if search_query and rakuten_app_id and search_query != st.session_state.last_search_query:
            with st.spinner('シリーズ情報を検索中...'):
                st.session_state.selected_book = None
                results = search_rakuten_books(search_query, rakuten_app_id, genre_id="001001", hits=20)
                st.session_state.search_results = results
                st.session_state.last_search_query = search_query 
                st.session_state.series_max_vol = 1
                if not results: st.warning("見つかりませんでした。")

        # 候補選択
        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"{r['title']}" for r in st.session_state.search_results]
            sel = st.selectbox("↓ シリーズを選択してください", opts, key="s_sel")
            
            if sel != "(選択してください)":
                current_sel = st.session_state.search_results[opts.index(sel)-1]
                
                # 選択が変わった時だけ詳細検索（API節約）
                if st.session_state.selected_book != current_sel:
                    st.session_state.selected_book = current_sel
                    # シリーズ名正規化して最新刊を探す
                    norm_title = normalize_title(current_sel['title'])
                    with st.spinner(f'「{norm_title}」の既刊情報を確認中...'):
                        max_vol, meta_info = get_series_stats(norm_title, rakuten_app_id)
                        st.session_state.series_max_vol = max_vol
                        # メタ情報が見つかれば更新、なければ検索結果のものを使う
                        if meta_info:
                            st.session_state.series_meta_info = meta_info
                        else:
                            st.session_state.series_meta_info = current_sel

    # 2. 一括登録（スライダー）エリア
    if st.session_state.selected_book:
        # メタ情報の準備
        meta = st.session_state.series_meta_info
        series_title = normalize_title(meta['title']) if meta else ""
        max_v = st.session_state.series_max_vol
        
        st.divider()
        st.subheader(f"📖 {series_title}")
        st.caption(f"最新刊は **{max_v}巻** あたりです")

        with st.form("bulk_reg"):
            st.write("所持している巻数を指定してください")
            
            # スライダー (最大値が1の場合は2にする)
            slider_limit = max(max_v, 2)
            owned_vol = st.slider("何巻まで持っていますか？", 1, slider_limit, 1)
            
            st.info(f"👉 **1巻 〜 {owned_vol}巻** を「所持」として登録します")
            
            genre = st.text_input("ジャンル", placeholder="少年, アクション", value="少年")

            if st.form_submit_button("一括登録する", type="primary"):
                added_count = 0
                for v in range(1, owned_vol + 1):
                    # 既に登録済みかチェック
                    exists = False
                    for existing in st.session_state.manga_data:
                        if existing['title'] == series_title and existing['volume'] == v:
                            exists = True
                            break
                    
                    if not exists:
                        new_d = {
                            "id": datetime.now().strftime("%Y%m%d%H%M%S") + str(v),
                            "title": series_title,
                            "volume": v,
                            "status": "own",   # 強制的に所持
                            "my_score": 0,
                            "genre": genre,
                            "is_finished": False,
                            "image": meta.get("image", ""),
                            "author": meta.get("author", ""),
                            "publisher": meta.get("publisher", ""),
                            "isbn": "", 
                            "link": meta.get("link", ""),
                            "releaseDate": ""
                        }
                        st.session_state.manga_data.append(new_d)
                        added_count += 1
                
                save_data(st.session_state.manga_data)
                
                if added_count > 0:
                    st.success(f"『{series_title}』を1〜{owned_vol}巻まで登録しました！（計{added_count}冊）")
                    st.session_state.search_results = []
                    st.session_state.selected_book = None
                    st.session_state.last_search_query = ""
                    st.rerun()
                else:
                    st.warning("指定された巻はすべて登録済みです。")

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
                    # 次巻追加ボタン
                    next_vol_num = int(series['max_vol']) + 1
                    if st.button(f"➕ Vol.{next_vol_num} 追加", key=f"add_n_{series['title']}"):
                        with st.spinner("検索中..."):
                            new_info = get_next_volume_info(series['title'], next_vol_num, rakuten_app_id)
                            base = series['meta']
                            new_entry = {
                                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                                "title": series['title'], "volume": next_vol_num, "status": "own",
                                "my_score": 0, "genre": base.get("genre", ""), "is_finished": False,
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

# --- 他のビュー ---
common_column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル", "volume": st.column_config.NumberColumn("巻", format="%d"),
    "releaseDate": st.column_config.TextColumn("発売日"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"]),
    "my_score": st.column_config.NumberColumn("評価", format="%d⭐"),
    "is_finished": st.column_config.CheckboxColumn("完"),
    "link": st.column_config.LinkColumn("Link"),
    "id": None, "author": None, "publisher": None, "isbn": None, "genre": None
}

if view_mode == "🏆 全件リスト":
    st.header("🏆 全件リスト")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data).sort_values(["my_score", "title"], ascending=[False, True])
        e_df = st.data_editor(df, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_all")
        if not df.equals(e_df): update_data(e_df); st.rerun()

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
