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
    
    # データ補完
    for d in data:
        d.setdefault('title', 'No Title')
        d.setdefault('status', 'own')
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
                
                if title:
                    results.append({
                        "title": title, "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""), "image": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""), "isbn": isbn, "releaseDate": info.get("salesDate", ""),
                        "source": "Rakuten"
                    })
        return results
    except: return []

def get_next_volume_info(series_title, next_vol, app_id):
    """次巻情報取得 (通常版優先)"""
    if not app_id: return None
    query = f"{series_title} {next_vol}"
    results = search_rakuten_books(query, app_id, hits=10, sort="+releaseDate")
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

# --- サイドバー ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio("表示モード", ["➕ 漫画登録＆ライブラリ", "🏆 全件リスト"])
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

@st.dialog("1冊の詳細編集")
def edit_single_book_dialog(item):
    """個別の本の編集用ダイアログ"""
    with st.form(f"edit_form_{item['id']}"):
        col1, col2 = st.columns([1, 2])
        with col1:
            if item.get("image"): st.image(item["image"], width=100)
            else: st.write("No Image")
        with col2:
            new_title = st.text_input("タイトル", item["title"])
            # 巻数スライダー
            new_vol = st.slider("巻数", min_value=1, max_value=max(200, item["volume"] + 10), value=item["volume"])
            new_date = st.text_input("発売日", item.get("releaseDate", ""))
            
            if st.form_submit_button("更新"):
                for d in st.session_state.manga_data:
                    if d['id'] == item['id']:
                        d['title'] = new_title; d['volume'] = new_vol; d['releaseDate'] = new_date
                        break
                save_data(st.session_state.manga_data)
                st.rerun()
            
            if st.form_submit_button("削除", type="primary"):
                st.session_state.manga_data = [d for d in st.session_state.manga_data if d['id'] != item['id']]
                save_data(st.session_state.manga_data)
                st.rerun()

@st.dialog("シリーズ詳細", width="large")
def series_detail_dialog(series_info):
    """
    シリーズ全体の所持巻一覧を表示するダイアログ
    """
    st.subheader(f"📖 {series_info['title']}")
    
    # --- 次巻追加エリア ---
    next_vol_num = int(series_info['max_vol']) + 1
    
    # ダイアログ内でのアクション用コンテナ
    col_add, col_link = st.columns([2, 1])
    with col_add:
        if st.button(f"➕ 次の巻 (Vol.{next_vol_num}) を追加", key=f"dlg_add_{series_info['title']}"):
            with st.spinner("検索中..."):
                new_info = get_next_volume_info(series_info['title'], next_vol_num, rakuten_app_id)
                base = series_info['meta']
                new_entry = {
                    "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                    "title": series_info['title'],
                    "volume": next_vol_num,
                    "status": "own",
                    "author": base.get("author", ""), "publisher": base.get("publisher", ""),
                    "image": new_info.get("image", "") if new_info else "",
                    "link": new_info.get("link", "") if new_info else "",
                    "isbn": new_info.get("isbn", "") if new_info else "",
                    "releaseDate": new_info.get("releaseDate", "") if new_info else ""
                }
                st.session_state.manga_data.append(new_entry)
                save_data(st.session_state.manga_data)
                st.toast(f"Vol.{next_vol_num} を追加しました！")
                st.rerun()
    
    with col_link:
        if series_info['link']:
            st.link_button("楽天で見る", series_info['link'])

    st.divider()

    # --- 所持巻リスト（グリッド表示） ---
    vol_cols = st.columns(4)
    for j, (idx, row) in enumerate(series_info['df'].iterrows()):
        with vol_cols[j % 4]:
            if row.get("image"):
                st.image(row["image"], use_container_width=True)
            else:
                st.caption("No Image")
            
            # 編集ボタン
            if st.button("編集", key=f"dlg_edit_{row['id']}"):
                edit_single_book_dialog(row.to_dict())
            
            st.caption(f"Vol.{row['volume']}")


# --- メインビュー ---
if view_mode == "➕ 漫画登録＆ライブラリ":
    st.header("漫画管理アプリ")
    
    if not rakuten_app_id:
        st.warning("⚠️ サイドバーで楽天Application IDを設定してください。")

    # === 1. 登録・検索エリア ===
    st.subheader("📚 漫画登録")
    
    with st.container():
        search_query = st.text_input("タイトル検索 (入力してEnter)", placeholder="例: 呪術廻戦", key="s_in")
        
        # 自動検索
        if search_query and rakuten_app_id and search_query != st.session_state.last_search_query:
            with st.spinner('検索中...'):
                st.session_state.selected_book = None
                results = search_rakuten_books(search_query, rakuten_app_id, genre_id="001001", hits=20)
                st.session_state.search_results = results
                st.session_state.last_search_query = search_query 
                if not results: st.warning("見つかりませんでした。")

        # 候補選択
        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"{r['title']}" for r in st.session_state.search_results]
            sel = st.selectbox("↓ 追加する本を選択", opts, key="s_sel")
            
            if sel != "(選択してください)":
                current_sel = st.session_state.search_results[opts.index(sel)-1]
                st.session_state.selected_book = current_sel

    # 登録フォーム
    init = {"title":"", "image":"", "author":"", "publisher":"", "isbn":"", "link":"", "volume": 1}
    
    if st.session_state.selected_book:
        init = st.session_state.selected_book.copy()
        init["volume"] = extract_volume(init["title"])
        init["title"] = normalize_title(init["title"])

    with st.form("reg"):
        col_img, col_form = st.columns([1, 3])
        
        with col_img:
            if init.get("image"): st.image(init["image"], width=100)
            else: st.info("No Image")
            
        with col_form:
            st.caption("以下の内容で登録します")
            title = st.text_input("タイトル (シリーズ名)", init["title"])
            # 巻数スライダー
            vol = st.slider("巻数", min_value=1, max_value=max(200, init["volume"] + 10), value=init["volume"])
            date = st.text_input("発売日", value=init.get("releaseDate", ""))
            
            submit = st.form_submit_button("追加", type="primary")

        if submit and title:
            new_d = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": title, "volume": vol, "status": "own",
                "image": init.get("image", ""), "author": init.get("author", ""),
                "publisher": init.get("publisher", ""), "isbn": init.get("isbn", ""), 
                "link": init.get("link", ""), "releaseDate": date
            }
            st.session_state.manga_data.append(new_d)
            save_data(st.session_state.manga_data)
            st.success(f"『{title}』 Vol.{vol} を追加しました！")
            
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()

    st.divider()
    
    # === 2. 本棚（シリーズ一覧）エリア ===
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
        
        # 更新順に並べる
        series_groups.sort(key=lambda x: x['last_updated'], reverse=True)
        
        # グリッド表示
        cols = st.columns(4)
        for i, series in enumerate(series_groups):
            col = cols[i % 4]
            with col:
                # 表紙画像 (1巻)
                if series['image']:
                    st.image(series['image'], use_container_width=True)
                else:
                    st.markdown(f"<div style='background:#eee;height:150px;text-align:center;padding:60px 0;'>No Img</div>", unsafe_allow_html=True)
                
                # タイトル
                st.markdown(f"**{series['title']}**")
                
                # 詳細を開くボタン（ダイアログ起動）
                count = len(series['df'])
                if st.button(f"📂 全{count}冊を見る", key=f"open_{series['title']}"):
                    series_detail_dialog(series)
                
                st.divider()
    else:
        st.info("登録された漫画はありません。")

# --- 他のビュー ---
common_column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル", "volume": st.column_config.NumberColumn("巻", format="%d"),
    "releaseDate": st.column_config.TextColumn("発売日"),
    "link": st.column_config.LinkColumn("Link"),
    "id": None, "author": None, "publisher": None, "isbn": None, "status": None, "genre": None
}

if view_mode == "🏆 全件リスト":
    st.header("🏆 全件リスト")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data).sort_values(["title", "volume"], ascending=[True, True])
        e_df = st.data_editor(df, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_all")
        if not df.equals(e_df): update_data(e_df); st.rerun()

st.divider()
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
    st.download_button("CSV保存", df.to_csv(index=False).encode('utf-8-sig'), "manga.csv", "text/csv")
