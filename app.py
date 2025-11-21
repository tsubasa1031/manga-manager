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

# --- 楽天ブックスAPI 関連関数 ---

def search_rakuten_books(query, app_id, genre_id="001001"):
    """
    楽天ブックスAPIで検索
    genre_id:
        '001001': 漫画 (コミック)
        '001': 本 (書籍全般)
        '003': DVD/Blu-ray (アニメ等)
        '006': ゲーム
        '': すべて
    """
    if not query or not app_id:
        return []

    url = "https://app.rakuten.co.jp/services/api/BooksTotal/Search/20170404"
    
    params = {
        "applicationId": app_id,
        "keyword": query, # titleではなくkeywordにすることで広く検索
        "hits": 20,
        "sort": "standard"
    }
    
    # ジャンル指定がある場合
    if genre_id:
        params["booksGenreId"] = genre_id

    results = []
    try:
        response = requests.get(url, params=params)
        data = response.json()
        
        if "Items" in data:
            for item in data["Items"]:
                info = item.get("Item", {})
                title = info.get("title", "")
                
                # 重複排除
                if title and not any(r['title'] == title for r in results):
                    results.append({
                        "title": title,
                        "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""),
                        "image": info.get("largeImageUrl", ""), # 修正: thumbnail -> image に統一
                        "link": info.get("itemUrl", ""),
                        "isbn": info.get("isbn", ""),
                        "source": "Rakuten"
                    })
        return results
    except Exception as e:
        return []

def fetch_date_rakuten(title, next_vol, app_id):
    """
    楽天APIで次巻の発売日を探す
    漫画ジャンル(001001)で、発売日が新しい順にソートして検索
    """
    if not app_id: return None
    
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {
        "applicationId": app_id,
        "title": f"{title} {next_vol}", # タイトル + 巻数
        "booksGenreId": "001001",      # 漫画に限定
        "hits": 1,
        "sort": "-releaseDate"         # 新しい順
    }
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if "Items" in data and len(data["Items"]) > 0:
            # 楽天の日付形式: "2023年10月04日" や "2023年10月"
            return data["Items"][0]["Item"].get("salesDate")
    except:
        pass
    return None


# --- アプリケーション本体 ---

st.set_page_config(page_title="漫画管理アプリ", layout="wide")

# セッションステート初期化
if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_book' not in st.session_state:
    st.session_state.selected_book = None

# --- サイドバー ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio(
        "表示モード",
        ["➕ 漫画登録", "🏆 全件リスト", "🆕 新着ビュー", "🔖 未読・欲しい", "💎 完結＆高評価", "🎨 ジャンル別"]
    )
    st.divider()
    st.header("⚙️ 設定")
    st.markdown("""
    検索には**楽天ウェブサービス**のApp IDが必要です。
    [こちらから発行](https://webservice.rakuten.co.jp/) (無料)
    """)
    rakuten_app_id = st.text_input("楽天 Application ID", type="password")
    st.caption("Data Source: Rakuten Books API")

# --- 共通関数: データ更新 ---
def update_data(edited_df):
    updated_list = edited_df.to_dict(orient="records")
    current_data_map = {d['id']: d for d in st.session_state.manga_data}
    for item in updated_list:
        if item['id'] in current_data_map:
            current_data_map[item['id']] = item
    st.session_state.manga_data = list(current_data_map.values())
    save_data(st.session_state.manga_data)

# --- カラム設定 ---
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
    
    if not rakuten_app_id:
        st.warning("⚠️ サイドバーで楽天Application IDを設定してください。")

    with st.container():
        col_s1, col_s2 = st.columns([3, 1])
        
        with col_s1:
            search_query = st.text_input("タイトル検索 (楽天)", placeholder="例: 呪術廻戦", key="s_in")
            
            # 楽天ブックスAPI用のジャンルフィルタ
            filter_option = st.radio(
                "検索ジャンル:",
                ["漫画 (Comic)", "書籍 (Books)", "アニメ (DVD/BD)", "ゲーム (Game)", "すべて"],
                index=0,
                horizontal=True
            )
            
            # ジャンルIDへのマッピング
            if "漫画" in filter_option: genre_id = "001001"
            elif "書籍" in filter_option: genre_id = "001"
            elif "アニメ" in filter_option: genre_id = "003" # DVD/Blu-ray
            elif "ゲーム" in filter_option: genre_id = "006"
            else: genre_id = ""

        with col_s2:
            st.write("")
            st.write("")
            search_clicked = st.button("🔍 検索", type="primary", disabled=not rakuten_app_id)

        if search_clicked and search_query and rakuten_app_id:
            with st.spinner('楽天ブックスで検索中...'):
                st.session_state.selected_book = None
                results = search_rakuten_books(search_query, rakuten_app_id, genre_id)
                st.session_state.search_results = results
                if not results: st.warning("見つかりませんでした。")

        if st.session_state.search_results:
            # リスト表示を見やすく
            opts = ["(選択してください)"] + [f"{r['title']} - {r['author']}" for r in st.session_state.search_results]
            sel = st.selectbox("候補を選択", opts, key="s_sel")
            if sel != "(選択してください)":
                st.session_state.selected_book = st.session_state.search_results[opts.index(sel)-1]

    # 入力フォーム
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
            
            r4, r5 = st.columns(2)
            genre = r4.text_input("ジャンル", placeholder="少年, アクション")
            date = r5.text_input("次巻発売日", placeholder="YYYY年MM月DD日")
            
            r6, r7 = st.columns(2)
            f_chk = r6.checkbox("完結済み")
            u_chk = r7.checkbox("未読")
            
            st.caption(f"著者: {init['author']} / 出版社: {init['publisher']}")

        with c2:
            # ここで init["image"] を参照する際に、以前の search_rakuten_books は "thumbnail" というキーを使っていたため
            # キーエラーが発生していました。search_rakuten_books 側を "image" に修正しました。
            if init.get("image"): 
                st.image(init["image"], width=100)
            else: 
                st.info("No Image")

        if st.form_submit_button("追加") and title:
            # 発売日自動取得 (楽天)
            if not date and rakuten_app_id:
                next_v = vol + 1
                fetched = fetch_date_rakuten(title, next_v, rakuten_app_id)
                if fetched: 
                    date = fetched
                    st.success(f"発売日発見: {fetched}")
                else:
                    st.warning("発売日が見つかりませんでした。")

            new_d = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": title, "volume": vol, "releaseDate": date, "status": status,
                "my_score": score, "genre": genre, "is_finished": f_chk, "is_unread": u_chk,
                "image": init.get("image", ""), "author": init.get("author", ""), "publisher": init.get("publisher", ""),
                "isbn": init.get("isbn", ""), "link": init.get("link", "")
            }
            st.session_state.manga_data.append(new_d)
            save_data(st.session_state.manga_data)
            st.success(f"『{title}』を追加しました")
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()


# --- ビュー定義 ---
if view_mode == "🏆 全件リスト":
    st.header("🏆 全件リスト")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df_s = df.sort_values(["my_score", "title"], ascending=[False, True])
        e_df = st.data_editor(df_s, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_all")
        if not df_s.equals(e_df): update_data(e_df); st.rerun()
    else: st.info("データなし")

if view_mode == "🆕 新着ビュー":
    st.header("🆕 新着ビュー")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df_n = df.sort_values("id", ascending=False)
        e_df = st.data_editor(df_n, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_new")
        if not df_n.equals(e_df): update_data(e_df); st.rerun()
    else: st.info("データなし")

if view_mode == "🔖 未読・欲しい":
    st.header("🔖 未読・欲しい")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df_u = df[(df['status']=='want')|(df['is_unread']==True)].sort_values("releaseDate", ascending=False)
        if not df_u.empty:
            e_df = st.data_editor(df_u, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_un")
            if not df_u.equals(e_df): update_data(e_df); st.rerun()
        else: st.success("未読なし！")
    else: st.info("データなし")

if view_mode == "💎 完結＆高評価":
    st.header("💎 完結＆高評価")
    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        df_m = df[(df['is_finished']==True)&(df['my_score']>=4)].sort_values("my_score", ascending=False)
        if not df_m.empty:
            e_df = st.data_editor(df_m, column_config=common_column_config, use_container_width=True, hide_index=True, key="e_mst")
            if not df_m.equals(e_df): update_data(e_df); st.rerun()
        else: st.info("該当作品なし")
    else: st.info("データなし")

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
    else: st.info("データなし")

st.divider()
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
    st.download_button("CSV保存", df.to_csv(index=False).encode('utf-8-sig'), "manga.csv", "text/csv")
