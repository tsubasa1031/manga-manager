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
    """JSONファイルからデータを読み込む（新項目への対応含む）"""
    data = []
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError:
            data = []
    
    # 既存データに新しいキーがない場合の互換性処理
    for d in data:
        d.setdefault('my_score', 0)      # 自己評価 (0-5)
        d.setdefault('genre', '未分類')   # ジャンル
        d.setdefault('is_finished', False) # 完結済みか
        d.setdefault('is_unread', False)   # 未読（積読）か
        # 必須項目の補完
        d.setdefault('title', 'No Title')
        d.setdefault('status', 'want')

    return data

def save_data(data):
    """データをJSONファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- API関連関数 (Google / Rakuten) ---
# (APIロジックは変更なし、そのまま利用)

def search_google_books(query):
    if not query: return []
    base_url = "https://www.googleapis.com/books/v1/volumes"
    results = []
    params = {"q": query, "maxResults": 20, "orderBy": "relevance", "langRestrict": "ja", "printType": "books"}
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
                        elif ident.get("type") == "ISBN_10": isbn = ident.get("identifier")
                    results.append({
                        "title": title,
                        "author": ", ".join(info.get("authors", ["不明"])),
                        "publisher": info.get("publisher", ""),
                        "thumbnail": thumbnail,
                        "link": info.get("canonicalVolumeLink", ""),
                        "isbn": isbn,
                        "source": "Google"
                    })
        return results
    except: return []

def fetch_date_google(title, next_vol):
    params = {"q": f'"{title}" {next_vol}', "orderBy": "newest", "langRestrict": "ja"}
    try:
        res = requests.get("https://www.googleapis.com/books/v1/volumes", params=params)
        data = res.json()
        if "items" in data: return data["items"][0]["volumeInfo"].get("publishedDate")
    except: pass
    return None

def search_rakuten_books(query, app_id):
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {"applicationId": app_id, "title": query, "booksGenreId": "001001", "hits": 15, "sort": "standard"}
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
                        "title": title,
                        "author": info.get("author", "不明"),
                        "publisher": info.get("publisherName", ""),
                        "thumbnail": info.get("largeImageUrl", ""),
                        "link": info.get("itemUrl", ""),
                        "isbn": info.get("isbn", ""),
                        "source": "Rakuten"
                    })
        return results
    except: return []

def fetch_date_rakuten(title, next_vol, app_id):
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

# セッションステート初期化
if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_book' not in st.session_state:
    st.session_state.selected_book = None

# --- サイドバー: ビュー切り替えと設定 ---
with st.sidebar:
    st.title("📚 メニュー")
    view_mode = st.radio(
        "表示モードを選択",
        [
            "➕ 漫画登録",
            "🏆 全件リスト (スコア順)",
            "🆕 新着ビュー",
            "🔖 未読・欲しいリスト",
            "💎 完結＆高評価",
            "🎨 ジャンル別ビュー"
        ]
    )
    
    st.divider()
    st.header("⚙️ 設定")
    rakuten_app_id = st.text_input("楽天 App ID", type="password", help="楽天ブックスAPIを利用する場合に入力")

# --- 共通関数: データ更新 ---
def update_data(edited_df, key_suffix=""):
    """編集されたDataFrameを保存する"""
    updated_list = edited_df.to_dict(orient="records")
    # IDをキーにして既存データを更新（存在しないIDは追加にはならないが、ここでは全置換ロジックに近い）
    # 編集対象外のデータを探すのが難しいため、IDベースでマージする
    
    current_data_map = {d['id']: d for d in st.session_state.manga_data}
    
    for item in updated_list:
        if item['id'] in current_data_map:
            current_data_map[item['id']] = item # 更新
            
    st.session_state.manga_data = list(current_data_map.values())
    save_data(st.session_state.manga_data)
    # st.toast("保存しました！") # 頻繁に出るとうざいのでコメントアウト可

# --- カラム設定 (共通) ---
common_column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル",
    "volume": st.column_config.NumberColumn("巻数", format="%d巻", width="small"),
    "releaseDate": st.column_config.TextColumn("次発売日", width="small"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"], required=True, width="small"),
    "my_score": st.column_config.NumberColumn("評価(1-5)", min_value=0, max_value=5, step=1, format="%d ⭐"),
    "genre": st.column_config.TextColumn("ジャンル", width="medium"),
    "is_finished": st.column_config.CheckboxColumn("完結", width="small"),
    "is_unread": st.column_config.CheckboxColumn("未読", width="small"),
    "link": st.column_config.LinkColumn("詳細", display_text="Link"),
    # 非表示項目
    "id": None, "author": None, "publisher": None, "isbn": None
}


# ==========================================
# ビュー 1: 漫画登録
# ==========================================
if view_mode == "➕ 漫画登録":
    st.header("漫画登録")
    
    # --- 検索エリア ---
    with st.container():
        col_s1, col_s2 = st.columns([3, 1])
        with col_s1:
            search_query = st.text_input("タイトル検索", placeholder="例: 呪術廻戦", key="search_input")
        with col_s2:
            st.write("")
            st.write("")
            search_clicked = st.button("🔍 検索", type="primary")

        if search_clicked and search_query:
            with st.spinner('検索中...'):
                st.session_state.selected_book = None
                if rakuten_app_id:
                    results = search_rakuten_books(search_query, rakuten_app_id)
                else:
                    results = search_google_books(search_query)
                st.session_state.search_results = results
                if not results: st.warning("見つかりませんでした。")

        if st.session_state.search_results:
            options = ["(選択してください)"] + [f"[{r['source']}] {r['title']} - {r['author']}" for r in st.session_state.search_results]
            selected_option = st.selectbox("候補を選択", options, key="search_select")
            if selected_option != "(選択してください)":
                index = options.index(selected_option) - 1
                st.session_state.selected_book = st.session_state.search_results[index]

    # --- 入力フォーム ---
    init = {"title": "", "image": "", "author": "", "publisher": "", "isbn": "", "link": ""}
    if st.session_state.selected_book: init = st.session_state.selected_book

    with st.form("reg_form", clear_on_submit=False):
        st.subheader("📝 登録詳細")
        col_f1, col_f2 = st.columns([2, 1])
        
        with col_f1:
            in_title = st.text_input("タイトル", value=init["title"])
            c1, c2, c3 = st.columns(3)
            with c1: in_vol = st.number_input("所持巻数", 1, step=1, value=1)
            with c2: in_status = st.selectbox("状態", ["own", "want"], format_func=lambda x: "持ってる" if x=="own" else "欲しい")
            with c3: in_score = st.slider("自己評価", 0, 5, 3)
            
            c4, c5 = st.columns(2)
            with c4: in_genre = st.text_input("ジャンル", placeholder="例: アクション, 少年漫画")
            with c5: in_date = st.text_input("次巻発売日", placeholder="YYYY年MM月DD日")

            c6, c7 = st.columns(2)
            with c6: in_finished = st.checkbox("完結済み？")
            with c7: in_unread = st.checkbox("まだ読んでない？(未読)")

        with col_f2:
            if init["image"]: st.image(init["image"], width=120)
            else: st.info("No Image")

        submitted = st.form_submit_button("リストに追加")

        if submitted and in_title:
            # 発売日自動取得
            if not in_date:
                next_vol = in_vol + 1
                fetched = None
                if rakuten_app_id: fetched = fetch_date_rakuten(in_title, next_vol, rakuten_app_id)
                if not fetched: fetched = fetch_date_google(in_title, next_vol)
                if fetched: 
                    in_date = fetched
                    st.success(f"発売日発見: {fetched}")

            new_entry = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": in_title,
                "volume": in_vol,
                "releaseDate": in_date,
                "status": in_status,
                "my_score": in_score,
                "genre": in_genre,
                "is_finished": in_finished,
                "is_unread": in_unread,
                "image": init["image"],
                "author": init["author"],
                "publisher": init["publisher"],
                "isbn": init["isbn"],
                "link": init["link"]
            }
            st.session_state.manga_data.append(new_entry)
            save_data(st.session_state.manga_data)
            st.success(f"『{in_title}』を追加しました！")
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()


# ==========================================
# データ準備 (DataFrame化)
# ==========================================
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
else:
    df = pd.DataFrame(columns=["id", "title", "volume", "releaseDate", "status", "my_score", "genre", "is_finished", "is_unread", "image", "link"])

# ==========================================
# ビュー 2: 全件リスト (スコア順)
# ==========================================
if view_mode == "🏆 全件リスト (スコア順)":
    st.header("🏆 おすすめランキング (自己評価順)")
    if not df.empty:
        # スコアが高い順、同じならタイトル順
        df_sorted = df.sort_values(by=["my_score", "title"], ascending=[False, True])
        
        edited_df = st.data_editor(
            df_sorted,
            column_config=common_column_config,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="editor_score"
        )
        if not df_sorted.equals(edited_df):
            update_data(edited_df)
            st.rerun()
    else:
        st.info("データがありません。")

# ==========================================
# ビュー 3: 新着ビュー
# ==========================================
if view_mode == "🆕 新着ビュー":
    st.header("🆕 最近登録したマンガ")
    st.caption("登録日が新しい順に表示しています。買ったばかりの本のチェックに。")
    if not df.empty:
        # ID (タイムスタンプ) の降順
        df_new = df.sort_values(by="id", ascending=False)
        
        edited_df = st.data_editor(
            df_new,
            column_config=common_column_config,
            use_container_width=True,
            hide_index=True,
            key="editor_new"
        )
        if not df_new.equals(edited_df):
            update_data(edited_df)
            st.rerun()
    else:
        st.info("データがありません。")

# ==========================================
# ビュー 4: 未読・欲しいリスト
# ==========================================
if view_mode == "🔖 未読・欲しいリスト":
    st.header("🔖 未読管理 & 欲しいものリスト")
    st.caption("「持ってるけど未読」または「欲しい」ステータスの本を表示します。")
    
    if not df.empty:
        # フィルタ条件: statusがwant または is_unreadがTrue
        mask = (df['status'] == 'want') | (df['is_unread'] == True)
        df_unread = df[mask].sort_values(by="releaseDate", ascending=False) # 発売日が近い/新しい順が見やすいかも
        
        if not df_unread.empty:
            edited_df = st.data_editor(
                df_unread,
                column_config=common_column_config,
                use_container_width=True,
                hide_index=True,
                key="editor_unread"
            )
            if not df_unread.equals(edited_df):
                update_data(edited_df)
                st.rerun()
        else:
            st.success("すべて読み終わっています！素晴らしい！")
    else:
        st.info("データがありません。")

# ==========================================
# ビュー 5: 完結＆高評価
# ==========================================
if view_mode == "💎 完結＆高評価":
    st.header("💎 完結済みの名作殿堂入り")
    st.caption("「完結済み」かつ「評価4以上」の作品だけを抽出。一気読みにおすすめ。")
    
    if not df.empty:
        # フィルタ: 完結 AND スコア>=4
        mask = (df['is_finished'] == True) & (df['my_score'] >= 4)
        df_masterpiece = df[mask].sort_values(by="my_score", ascending=False)
        
        if not df_masterpiece.empty:
            edited_df = st.data_editor(
                df_masterpiece,
                column_config=common_column_config,
                use_container_width=True,
                hide_index=True,
                key="editor_master"
            )
            if not df_masterpiece.equals(edited_df):
                update_data(edited_df)
                st.rerun()
        else:
            st.info("条件に合う「完結済みの高評価作品」はまだありません。")
    else:
        st.info("データがありません。")

# ==========================================
# ビュー 6: ジャンル別ビュー (カンバン風)
# ==========================================
if view_mode == "🎨 ジャンル別ビュー":
    st.header("🎨 ジャンル別ライブラリ")
    st.caption("登録されたジャンルごとに整理して表示します。")

    if not df.empty:
        # ジャンルを抽出（カンマ区切り対応）
        all_genres = set()
        for g_str in df['genre'].unique():
            if g_str:
                for g in g_str.replace('、', ',').split(','): # 読点とカンマに対応
                    all_genres.add(g.strip())
        
        # 「未分類」も追加
        if "" in all_genres: all_genres.remove("")
        sorted_genres = sorted(list(all_genres))
        if "未分類" not in sorted_genres: sorted_genres.append("未分類")

        # ジャンルごとに表示（Streamlitでカンバンは列で再現）
        # 列数が多くなりすぎないように、エクスパンダーか、2列レイアウトで順次表示
        
        for genre in sorted_genres:
            # そのジャンルを含むデータを抽出
            if genre == "未分類":
                mask = (df['genre'] == "") | (df['genre'] == "未分類")
            else:
                mask = df['genre'].str.contains(genre, na=False)
            
            df_genre = df[mask].sort_values(by="my_score", ascending=False)
            
            if not df_genre.empty:
                with st.expander(f"📂 {genre} ({len(df_genre)}冊)", expanded=True):
                    # ここでは編集不可の表示のみ（編集は全件リストでやってもらう方が安全）
                    # もし編集させたい場合はキーをユニークにする必要がある
                    st.dataframe(
                        df_genre,
                        column_config=common_column_config,
                        use_container_width=True,
                        hide_index=True
                    )
    else:
        st.info("データがありません。")

# --- フッター: CSV DL ---
st.divider()
if not df.empty:
    csv = df.to_csv(index=False, encoding='utf_8_sig')
    st.download_button("📥 データをCSVでバックアップ", csv, "manga_backup.csv", "text/csv")
