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
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_data(data):
    """データをJSONファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# --- Google Books API (フォールバック用) ---
def search_google_books(query):
    """
    Google Books APIから情報を取得（Qiita記事のロジックを反映）
    - ISBNの取得
    - 画像URLのhttps化
    - 著者、出版社、リンク情報の取得
    """
    if not query: return []
    base_url = "https://www.googleapis.com/books/v1/volumes"
    results = []
    # maxResultsは記事に合わせて少し多めに取得
    params = {"q": query, "maxResults": 20, "orderBy": "relevance", "langRestrict": "ja", "printType": "books"}
    
    try:
        response = requests.get(base_url, params=params)
        data = response.json()
        if "items" in data:
            for item in data["items"]:
                info = item.get("volumeInfo", {})
                title = info.get("title", "")
                
                # 重複チェック
                if title and not any(r['title'] == title for r in results):
                    # --- 画像URLのhttps化 (記事のgsub("http", "https")に相当) ---
                    thumbnail = info.get("imageLinks", {}).get("thumbnail", "")
                    if thumbnail.startswith("http://"):
                        thumbnail = thumbnail.replace("http://", "https://")
                    
                    # --- ISBNの取得 (記事のindustryIdentifiers処理に相当) ---
                    isbn = ""
                    identifiers = info.get("industryIdentifiers", [])
                    for ident in identifiers:
                        # ISBN_13を優先、なければISBN_10
                        if ident.get("type") == "ISBN_13":
                            isbn = ident.get("identifier")
                            break
                        elif ident.get("type") == "ISBN_10":
                            isbn = ident.get("identifier")
                    
                    # 結果に追加
                    results.append({
                        "title": title,
                        "author": ", ".join(info.get("authors", ["不明"])), # 配列をカンマ区切り文字列に
                        "publisher": info.get("publisher", ""),
                        "thumbnail": thumbnail,
                        "link": info.get("canonicalVolumeLink", ""), # 詳細リンク
                        "isbn": isbn,
                        "source": "Google"
                    })
        return results
    except Exception as e:
        # エラー時は空リストを返す
        return []

def fetch_date_google(title, next_vol):
    """次巻の発売日をGoogle Books APIで検索"""
    params = {"q": f'"{title}" {next_vol}', "orderBy": "newest", "langRestrict": "ja"}
    try:
        res = requests.get("https://www.googleapis.com/books/v1/volumes", params=params)
        data = res.json()
        if "items" in data:
            # 最も関連度が高い（または新しい）項目の発売日を返す
            return data["items"][0]["volumeInfo"].get("publishedDate")
    except:
        pass
    return None

# --- 楽天ブックスAPI (メイン用) ---
def search_rakuten_books(query, app_id):
    """楽天ブックスAPIで検索（こちらも情報をリッチにする）"""
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {
        "applicationId": app_id,
        "title": query,
        "booksGenreId": "001001", # コミック
        "hits": 15,
        "sort": "standard" 
    }
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
    except Exception as e:
        return []

def fetch_date_rakuten(title, next_vol, app_id):
    """楽天APIで次巻の発売日を探す"""
    url = "https://app.rakuten.co.jp/services/api/BooksBook/Search/20170404"
    params = {
        "applicationId": app_id,
        "title": f"{title} {next_vol}", 
        "booksGenreId": "001001",
        "hits": 1,
        "sort": "-releaseDate" # 新しい順
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
st.title("📚 漫画管理アプリ")

# セッションステート初期化
if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_book' not in st.session_state:
    st.session_state.selected_book = None

# --- サイドバー: 設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    st.markdown("楽天App IDがあれば入力してください（精度向上）")
    rakuten_app_id = st.text_input("楽天 Application ID", type="password")
    st.caption("[楽天ID発行はこちら](https://webservice.rakuten.co.jp/)")

# --- 1. 漫画登録セクション ---
st.header("漫画登録")

# --- A. 検索エリア ---
with st.container():
    col_search_input, col_search_btn = st.columns([3, 1])
    with col_search_input:
        search_query = st.text_input("漫画名検索", placeholder="例: 呪術廻戦", key="search_input")
    with col_search_btn:
        st.write("") 
        st.write("") 
        search_clicked = st.button("🔍 検索", type="primary")

    if search_clicked and search_query:
        with st.spinner('検索中...'):
            st.session_state.selected_book = None
            # 楽天IDがあれば楽天、なければGoogle
            if rakuten_app_id:
                results = search_rakuten_books(search_query, rakuten_app_id)
            else:
                results = search_google_books(search_query)
            
            st.session_state.search_results = results
            if not results:
                st.warning("候補が見つかりませんでした。")

    # 結果選択
    if st.session_state.search_results:
        options = ["(選択してください)"] + [
            f"[{r['source']}] {r['title']} - {r['author']}" 
            for r in st.session_state.search_results
        ]
        
        selected_option = st.selectbox("↓ 候補から選択してください", options, key="search_select")
        
        if selected_option and selected_option != "(選択してください)":
            index = options.index(selected_option) - 1
            st.session_state.selected_book = st.session_state.search_results[index]

# --- B. 入力フォーム ---
# 初期値の準備
init = {"title": "", "image": "", "author": "", "publisher": "", "isbn": "", "link": ""}
if st.session_state.selected_book:
    init = st.session_state.selected_book

with st.form("register_form", clear_on_submit=False):
    st.markdown("#### 📝 登録内容")
    col_form, col_img = st.columns([2, 1])
    
    with col_form:
        input_title = st.text_input("タイトル", value=init["title"])
        
        c1, c2 = st.columns(2)
        with c1:
            input_volume = st.number_input("最新の所持巻数", min_value=1, step=1, value=1)
        with c2:
            input_status = st.selectbox("状態", ["own", "want"], format_func=lambda x: "持ってる" if x == "own" else "欲しい")
            
        input_date = st.text_input("次巻発売日 (空欄で自動取得)", placeholder="YYYY年MM月DD日")
        
        # 隠しフィールド的に表示（編集不可にするか、情報として出す）
        st.caption(f"著者: {init['author']} / 出版社: {init['publisher']}")
        st.caption(f"ISBN: {init['isbn']}")

    with col_img:
        if init["image"]:
            st.image(init["image"], caption="表紙", width=120)
        else:
            st.info("No Image")

    submitted = st.form_submit_button("リストに追加する")

    if submitted:
        if not input_title:
            st.error("タイトルを入力してください。")
        else:
            # 発売日自動取得
            if not input_date:
                with st.spinner(f'次巻情報を検索中...'):
                    fetched_date = None
                    next_vol = input_volume + 1
                    if rakuten_app_id:
                        fetched_date = fetch_date_rakuten(input_title, next_vol, rakuten_app_id)
                    if not fetched_date:
                        fetched_date = fetch_date_google(input_title, next_vol)

                    if fetched_date:
                        input_date = fetched_date
                        st.success(f"発売日が見つかりました: {fetched_date}")
                    else:
                        input_date = "不明"

            # 保存データ作成（メタデータも含める）
            new_entry = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": input_title,
                "volume": input_volume,
                "releaseDate": input_date,
                "status": input_status,
                "image": init["image"], # HTTPS化済みのURL
                "author": init["author"],
                "publisher": init["publisher"],
                "isbn": init["isbn"],
                "link": init["link"]
            }
            
            st.session_state.manga_data.append(new_entry)
            save_data(st.session_state.manga_data)
            st.success(f"『{input_title}』を追加しました！")
            
            # リセット
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()

st.divider()

# --- 2. リスト表示 ---
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
else:
    # カラム定義（新しく追加したフィールドも）
    df = pd.DataFrame(columns=["id", "title", "volume", "releaseDate", "status", "image", "author", "publisher", "isbn", "link"])

tab1, tab2 = st.tabs(["📘 持ってる漫画", "🌟 欲しい漫画"])

# 表示カラム設定
column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル",
    "volume": st.column_config.NumberColumn("最新巻数", format="%d巻"),
    "releaseDate": st.column_config.TextColumn("次巻発売日"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"], required=True),
    # 追加情報（必要に応じて表示・非表示）
    "author": "著者",
    "publisher": "出版社",
    "isbn": None, # ISBNは普段は見なくていいので隠す
    "link": st.column_config.LinkColumn("詳細"),
    "id": None
}

def update_data(edited_df, original_status):
    updated_list = edited_df.to_dict(orient="records")
    # 編集されなかった他のステータスのデータを維持しつつ結合
    other_status_data = [d for d in st.session_state.manga_data if d['status'] != original_status]
    final_data = other_status_data + updated_list
    st.session_state.manga_data = final_data
    save_data(final_data)

with tab1:
    df_own = df[df['status'] == 'own']
    if not df_own.empty:
        edited_df_own = st.data_editor(
            df_own, column_config=column_config, num_rows="dynamic", use_container_width=True, key="editor_own", hide_index=True
        )
        if not df_own.equals(edited_df_own):
            update_data(edited_df_own, "own")
            st.rerun()

with tab2:
    df_want = df[df['status'] == 'want']
    if not df_want.empty:
        edited_df_want = st.data_editor(
            df_want, column_config=column_config, num_rows="dynamic", use_container_width=True, key="editor_want", hide_index=True
        )
        if not df_want.equals(edited_df_want):
            update_data(edited_df_want, "want")
            st.rerun()

# --- 3. CSVダウンロード ---
if st.session_state.manga_data:
    # CSVには隠しているISBNなども含めて出力
    csv_df = pd.DataFrame(st.session_state.manga_data).drop(columns=['id'])
    csv = csv_df.to_csv(index=False, encoding='utf_8_sig')
    st.download_button("📥 CSVダウンロード", csv, "manga_list.csv", "text/csv")
