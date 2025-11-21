import streamlit as st
import pandas as pd
import json
import os
import requests
import re
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

def normalize_title(title):
    """タイトルから巻数を除去してシリーズ名を抽出する"""
    if not title: return ""
    # 全角数字を半角に変換
    title = title.translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    
    # 末尾の巻数パターンを除去（例: "呪術廻戦 26", "ONE PIECE 100", "タイトル(3)"）
    # 1. 末尾のスペース+数字
    title = re.sub(r'\s*\d+$', '', title)
    # 2. 末尾の(数字) または （数字）
    title = re.sub(r'\s*\(\d+\)$', '', title)
    title = re.sub(r'\s*（\d+）$', '', title)
    # 3. 第N巻
    title = re.sub(r'\s*第?\d+巻$', '', title)
    
    return title.strip()

# --- 楽天ブックスAPI 関連関数 ---

def search_rakuten_books(query, app_id, genre_id="001001"):
    if not query or not app_id:
        return []

    # 登録済みのISBNリストを作成（重複除外用）
    registered_isbns = set()
    if 'manga_data' in st.session_state:
        for d in st.session_state.manga_data:
            if d.get('isbn'):
                registered_isbns.add(d['isbn'])

    url = "https://app.rakuten.co.jp/services/api/BooksTotal/Search/20170404"
    
    params = {
        "applicationId": app_id,
        "keyword": query,
        "hits": 30,
        "sort": "+releaseDate" # 発売日が古い順（1巻から並ぶように設定）
    }
    
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
                isbn = info.get("isbn", "")
                
                if isbn and isbn in registered_isbns:
                    continue

                if title and not any(r['title'] == title for r in results):
                    results.append({
                        "title": title,
                        "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""),
                        "image": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""),
                        "isbn": isbn,
                        "source": "Rakuten"
                    })
        return results
    except Exception as e:
        return []

def fetch_date_rakuten(title, next_vol, app_id):
    if not app_id: return None
    
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {
        "applicationId": app_id,
        "title": f"{title} {next_vol}",
        "booksGenreId": "001001",
        "hits": 1,
        "sort": "-releaseDate"
    }
    try:
        response = requests.get(url, params=params)
        data = response.json()
        if "Items" in data and len(data["Items"]) > 0:
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
        ["➕ 漫画登録＆ライブラリ", "🏆 全件リスト(表形式)", "🆕 新着ビュー", "🔖 未読・欲しい", "💎 完結＆高評価", "🎨 ジャンル別"]
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

# --- 共通関数: 1冊の詳細編集ダイアログ ---
@st.dialog("詳細編集")
def edit_dialog(item):
    with st.form(f"edit_form_{item['id']}"):
        col1, col2 = st.columns([1, 2])
        with col1:
            if item.get("image"):
                st.image(item["image"], width=100)
            else:
                st.write("No Image")
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
                        d['title'] = new_title
                        d['volume'] = new_vol
                        d['status'] = new_status
                        d['my_score'] = new_score
                        d['releaseDate'] = new_date
                        d['is_unread'] = new_unread
                        break
                save_data(st.session_state.manga_data)
                st.rerun()
            
            if st.form_submit_button("削除", type="primary"):
                st.session_state.manga_data = [d for d in st.session_state.manga_data if d['id'] != item['id']]
                save_data(st.session_state.manga_data)
                st.rerun()

# --- メインビュー: 漫画登録 ＆ ギャラリー ---
if view_mode == "➕ 漫画登録＆ライブラリ":
    st.header("漫画登録")
    
    if not rakuten_app_id:
        st.warning("⚠️ サイドバーで楽天Application IDを設定してください。")

    # --- 1. 登録フォームエリア ---
    with st.container():
        col_s1, col_s2 = st.columns([3, 1])
        with col_s1:
            search_query = st.text_input("タイトル検索 (楽天)", placeholder="例: 呪術廻戦", key="s_in")
            filter_option = st.radio(
                "検索ジャンル:", ["漫画 (Comic)", "書籍 (Books)", "アニメ (DVD/BD)", "ゲーム (Game)", "すべて"],
                index=0, horizontal=True
            )
            if "漫画" in filter_option: genre_id = "001001"
            elif "書籍" in filter_option: genre_id = "001"
            elif "アニメ" in filter_option: genre_id = "003"
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
                if not results: st.warning("見つかりませんでした（すべて登録済みか、該当なし）。")

        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"{r['title']} - {r['author']}" for r in st.session_state.search_results]
            sel = st.selectbox("候補を選択", opts, key="s_sel")
            if sel != "(選択してください)":
                st.session_state.selected_book = st.session_state.search_results[opts.index(sel)-1]

    init = {"title":"", "image":"", "author":"", "publisher":"", "isbn":"", "link":""}
    if st.session_state.selected_book: 
        init = st.session_state.selected_book
        # 選択されたら、自動的にタイトルから巻数を削除して「シリーズ名」としてセット
        init["title"] = normalize_title(init["title"])

    with st.form("reg"):
        c1, c2 = st.columns([2, 1])
        with c1:
            st.caption("※シリーズ名として登録します（巻数は削除推奨）")
            title = st.text_input("タイトル (シリーズ名)", init["title"])
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
        with c2:
            if init.get("image"): st.image(init["image"], width=100)
            else: st.info("No Image")

        if st.form_submit_button("追加") and title:
            if not date and rakuten_app_id:
                next_v = vol + 1
                fetched = fetch_date_rakuten(title, next_v, rakuten_app_id)
                if fetched: date = fetched
            
            new_d = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": title, "volume": vol, "releaseDate": date, "status": status,
                "my_score": score, "genre": genre, "is_finished": f_chk, "is_unread": u_chk,
                "image": init.get("image", ""), "author": init.get("author", ""),
                "publisher": init.get("publisher", ""), "isbn": init.get("isbn", ""), "link": init.get("link", "")
            }
            st.session_state.manga_data.append(new_d)
            save_data(st.session_state.manga_data)
            st.success(f"『{title}』を追加しました")
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()

    st.divider()
    
    # --- 2. ギャラリー（本棚）表示エリア ---
    st.subheader("📚 本棚")

    if st.session_state.manga_data:
        df = pd.DataFrame(st.session_state.manga_data)
        
        # シリーズ名（タイトルから巻数を除去）でグルーピング
        # データ上のtitleが既にシリーズ名になっている場合はそのままだが、
        # 過去データで巻数が入っている場合のためにnormalizeを適用してグルーピングキーを作る
        df['series_key'] = df['title'].apply(normalize_title)
        
        # シリーズごとのリストを作成
        series_groups = []
        for key, group in df.groupby('series_key'):
            # そのシリーズの中で一番若い巻（通常1巻）の画像を探す
            min_vol_row = group.loc[group['volume'].idxmin()]
            cover_image = min_vol_row.get('image', '')
            
            # シリーズ内の最新更新日時（ソート用）
            last_updated = group['id'].max()
            
            # シリーズ名（キーではなく、登録されているタイトルを使う。複数ある場合は一番多いものや最初のものを採用）
            # ここでは代表としてキー（正規化されたタイトル）を表示用にする
            display_title = key
            if not display_title: display_title = "No Title"

            series_groups.append({
                "title": display_title,
                "df": group.sort_values("volume"),
                "image": cover_image,
                "last_updated": last_updated
            })
        
        # 更新順（新しい順）にソート
        series_groups.sort(key=lambda x: x['last_updated'], reverse=True)

        # グリッド表示（3列）
        cols_per_row = 4
        cols = st.columns(cols_per_row)

        for i, series in enumerate(series_groups):
            col = cols[i % cols_per_row]
            with col:
                # --- 1. 表紙画像（フォルダの入り口） ---
                if series['image']:
                    st.image(series['image'], use_container_width=True)
                else:
                    # 画像がない場合のダミー
                    st.markdown(
                        f"""<div style='background-color:#eee;height:150px;display:flex;align-items:center;justify_content:center;color:#888;'>No Image</div>""", 
                        unsafe_allow_html=True
                    )
                
                # --- 2. フォルダ（Expander） ---
                # タイトルと巻数情報をラベルにする
                count = len(series['df'])
                with st.expander(f"📂 {series['title']} ({count})"):
                    # Expanderの中身：各巻のリスト表示
                    for _, row in series['df'].iterrows():
                        # 小さいグリッドで各巻を表示
                        c1, c2 = st.columns([1, 3])
                        with c1:
                            st.markdown(f"**Vol.{row['volume']}**")
                        with c2:
                             if st.button("編集", key=f"edit_{row['id']}"):
                                 edit_dialog(row.to_dict())
                        
                        # 発売日やステータスを小さく表示
                        st.caption(f"{'✅持' if row['status']=='own' else '🙏欲'} | {row['releaseDate'] or '発売日未定'}")
                        st.divider()

    else:
        st.info("まだ漫画が登録されていません。上のフォームから追加してください。")


# --- その他のビュー (表形式等は維持) ---
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

if view_mode == "🏆 全件リスト(表形式)":
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
