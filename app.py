import streamlit as st
import pandas as pd
import json
import os
import requests
from datetime import datetime

# --- 設定 ---
DATA_FILE = 'manga_data.json'

# --- 関数定義 ---

def load_data():
    """JSONファイルからデータを読み込む"""
    data = []
    if os.path.exists(DATA_FILE):
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
    """データをJSONファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- API関連関数 ---

def search_google_books(query):
    if not query: return []
    base_url = "https://www.googleapis.com/books/v1/volumes"
    results = []
    params = {"q": query, "maxResults": 10, "orderBy": "relevance", "langRestrict": "ja", "printType": "books"}
    try:
        response = requests.get(base_url, params=params)
        data = response.json()
        if "items" in data:
            for item in data["items"]:
                info = item.get("volumeInfo", {})
                title = info.get("title", "")
                if title and not any(r['title'] == title for r in results):
                    thumbnail = info.get("imageLinks", {}).get("thumbnail", "")
                    if thumbnail.startswith("http://"):
                        thumbnail = thumbnail.replace("http://", "https://")
                    isbn = ""
                    for ident in info.get("industryIdentifiers", []):
                        if ident.get("type") == "ISBN_13": isbn = ident.get("identifier"); break
                    results.append({
                        "title": title, "author": ", ".join(info.get("authors", ["不明"])),
                        "publisher": info.get("publisher", ""), "thumbnail": thumbnail,
                        "link": info.get("canonicalVolumeLink", ""), "isbn": isbn, "source": "Google"
                    })
        return results
    except: return []

def search_rakuten_books(query, app_id):
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {"applicationId": app_id, "title": query, "booksGenreId": "001001", "hits": 10, "sort": "standard"}
    results = []
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if "Items" in data:
            for item in data["Items"]:
                info = item.get("Item", {})
                title = info.get("title", "")
                if title and not any(r['title'] == title for r in results):
                    results.append({
                        "title": title, "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""), "thumbnail": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""), "isbn": info.get("isbn", ""), "source": "Rakuten"
                    })
        return results
    except: return []

def search_madb(query):
    """
    メディア芸術データベース(MADB)をSPARQLで検索する
    Endpoint: https://mediaarts-db.artmuseums.go.jp/sparql
    """
    endpoint = "https://mediaarts-db.artmuseums.go.jp/sparql"
    # マンガ(Book)でタイトルにキーワードを含むものを検索
    sparql_query = f"""
    PREFIX schema: <https://schema.org/>
    SELECT DISTINCT ?name ?author ?publisher ?date
    WHERE {{
      ?s a schema:Book ;
         schema:name ?name .
      FILTER(CONTAINS(?name, "{query}"))
      OPTIONAL {{ ?s schema:author/schema:name ?author . }}
      OPTIONAL {{ ?s schema:publisher/schema:name ?publisher . }}
      OPTIONAL {{ ?s schema:datePublished ?date . }}
    }}
    ORDER BY DESC(?date)
    LIMIT 10
    """
    
    try:
        response = requests.post(endpoint, data={'query': sparql_query}, headers={'Accept': 'application/sparql-results+json'})
        data = response.json()
        results = []
        for item in data['results']['bindings']:
            title = item['name']['value']
            if not any(r['title'] == title for r in results):
                results.append({
                    "title": title,
                    "author": item.get('author', {}).get('value', '不明'),
                    "publisher": item.get('publisher', {}).get('value', ''),
                    "thumbnail": "", # MADBは書影APIが特殊なため今回は空
                    "link": "https://mediaarts-db.artmuseums.go.jp/", 
                    "isbn": "", # 必要なら取得可
                    "source": "MADB" # メディア芸術DB
                })
        return results
    except Exception as e:
        return []

def fetch_date_google(title, next_vol):
    # (省略せず残す)
    params = {"q": f'"{title}" {next_vol}', "orderBy": "newest", "langRestrict": "ja"}
    try:
        res = requests.get("https://www.googleapis.com/books/v1/volumes", params=params)
        data = res.json()
        if "items" in data: return data["items"][0]["volumeInfo"].get("publishedDate")
    except: pass
    return None

def fetch_date_rakuten(title, next_vol, app_id):
    # (省略せず残す)
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {"applicationId": app_id, "title": f"{title} {next_vol}", "booksGenreId": "001001", "hits": 1, "sort": "-releaseDate"}
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if "Items" in data and len(data["Items"]) > 0: return data["Items"][0]["Item"].get("salesDate")
    except: pass
    return None

# --- アプリケーション本体 ---

st.set_page_config(page_title="漫画管理アプリ", layout="wide")

if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_book' not in st.session_state:
    st.session_state.selected_book = None

# --- サイドバー ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio("表示モード", ["➕ 漫画登録", "🏆 全件リスト", "🆕 新着ビュー", "🔖 未読・欲しい", "💎 完結＆高評価", "🎨 ジャンル別"])
    st.divider()
    st.header("⚙️ 設定")
    rakuten_app_id = st.text_input("楽天 App ID", type="password")
    use_madb = st.checkbox("メディア芸術DBも検索する", value=True, help="日本の公式アーカイブを検索します(少し時間がかかる場合があります)")

def update_data(edited_df):
    updated_list = edited_df.to_dict(orient="records")
    current_data_map = {d['id']: d for d in st.session_state.manga_data}
    for item in updated_list:
        if item['id'] in current_data_map:
            current_data_map[item['id']] = item
    st.session_state.manga_data = list(current_data_map.values())
    save_data(st.session_state.manga_data)

common_column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル",
    "volume": st.column_config.NumberColumn("巻数", format="%d巻", width="small"),
    "releaseDate": st.column_config.TextColumn("発売日", width="small"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"], width="small"),
    "my_score": st.column_config.NumberColumn("評価", format="%d⭐"),
    "is_finished": st.column_config.CheckboxColumn("完結", width="small"),
    "is_unread": st.column_config.CheckboxColumn("未読", width="small"),
    "link": st.column_config.LinkColumn("Link"),
    "id": None, "author": None, "publisher": None, "isbn": None, "genre": None
}

# --- 1. 漫画登録 ---
if view_mode == "➕ 漫画登録":
    st.header("漫画登録")
    with st.container():
        c1, c2 = st.columns([3, 1])
        search_query = c1.text_input("タイトル検索", key="s_in")
        if c2.button("🔍 検索", type="primary") and search_query:
            with st.spinner('検索中...'):
                st.session_state.selected_book = None
                results = []
                
                # 1. 楽天 or Google
                if rakuten_app_id:
                    results += search_rakuten_books(search_query, rakuten_app_id)
                else:
                    results += search_google_books(search_query)
                
                # 2. メディア芸術DB (オプション)
                if use_madb:
                    results += search_madb(search_query)
                
                st.session_state.search_results = results
                if not results: st.warning("見つかりませんでした。")

        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"[{r['source']}] {r['title']} - {r['author']}" for r in st.session_state.search_results]
            sel = st.selectbox("候補", opts, key="s_sel")
            if sel != "(選択してください)":
                st.session_state.selected_book = st.session_state.search_results[opts.index(sel)-1]

    init = {"title":"", "image":"", "author":"", "publisher":"", "isbn":"", "link":""}
    if st.session_state.selected_book: init = st.session_state.selected_book

    with st.form("reg"):
        st.subheader("詳細入力")
        c1, c2 = st.columns([2, 1])
        with c1:
            title = st.text_input("タイトル", init["title"])
            r1, r2, r3 = st.columns(3)
            vol = r1.number_input("巻数", 1, step=1, value=1)
            status = r2.selectbox("状態", ["own", "want"])
            score = r3.slider("評価", 0, 5, 3)
            genre = st.text_input("ジャンル", placeholder="少年, アクション")
            date = st.text_input("次巻発売日", placeholder="YYYY-MM-DD")
            f_chk = st.checkbox("完結済み")
            u_chk = st.checkbox("未読")
            if init['source'] == "MADB":
                st.caption("※メディア芸術DBのデータ出典: 独立行政法人国立美術館国立アートリサーチセンター「メディア芸術データベース」")
        with c2:
            if init["image"]: st.image(init["image"], width=100)
            else: st.info("No Image")

        if st.form_submit_button("追加") and title:
            if not date:
                next_v = vol + 1
                fd = None
                if rakuten_app_id: fd = fetch_date_rakuten(title, next_v, rakuten_app_id)
                if not fd: fd = fetch_date_google(title, next_v)
                if fd: date = fd; st.success(f"発売日: {fd}")
            
            new_d = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": title, "volume": vol, "releaseDate": date, "status": status,
                "my_score": score, "genre": genre, "is_finished": f_chk, "is_unread": u_chk,
                "image": init["image"], "author": init["author"], "publisher": init["publisher"],
                "isbn": init["isbn"], "link": init["link"]
            }
            st.session_state.manga_data.append(new_d)
            save_data(st.session_state.manga_data)
            st.success(f"『{title}』を追加しました")
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()

# --- 他のビュー (ロジックは同じなので省略せず記述) ---
if view_mode == "🏆 全件リスト":
    st.header("🏆 全件リスト")
    if not df.empty:
        df_s = df.sort_values(["my_score", "title"], ascending=[False, True])
        e_df = st.data_editor(df_s, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_all")
        if not df_s.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "🆕 新着ビュー":
    st.header("🆕 新着ビュー")
    if not df.empty:
        df_n = df.sort_values("id", ascending=False)
        e_df = st.data_editor(df_n, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_new")
        if not df_n.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "🔖 未読・欲しい":
    st.header("🔖 未読・欲しい")
    if not df.empty:
        df_u = df[(df['status']=='want')|(df['is_unread']==True)].sort_values("releaseDate", ascending=False)
        e_df = st.data_editor(df_u, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_un")
        if not df_u.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "💎 完結＆高評価":
    st.header("💎 完結＆高評価")
    if not df.empty:
        df_m = df[(df['is_finished']==True)&(df['my_score']>=4)].sort_values("my_score", ascending=False)
        e_df = st.data_editor(df_m, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_mst")
        if not df_m.equals(e_df): update_data(e_df); st.rerun()

if view_mode == "🎨 ジャンル別":
    st.header("🎨 ジャンル別")
    if not df.empty:
        genres = set()
        for g in df['genre'].unique():
            for sub in g.replace('、',',').split(','): genres.add(sub.strip())
        if "" in genres: genres.remove("")
        g_list = sorted(list(genres)) + ["未分類"]
        for g in g_list:
            mask = (df['genre']=="") if g=="未分類" else df['genre'].str.contains(g, na=False)
            df_g = df[mask].sort_values("my_score", ascending=False)
            if not df_g.empty:
                with st.expander(f"{g} ({len(df_g)})", expanded=True):
                    st.dataframe(df_g, column_config=common_column_config, use_container_width=True, hide_index=True)

st.divider()
if not df.empty:
    st.download_button("CSV保存", df.to_csv(index=False).encode('utf-8-sig'), "manga.csv", "text/csv")
