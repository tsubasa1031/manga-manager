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

def search_books_api(query):
    """
    Google Books APIで本を検索し、
    {タイトル, 画像URL, 最新巻数(推定), 著者} のリストを返す
    """
    if not query:
        return []
    
    base_url = "https://www.googleapis.com/books/v1/volumes"
    results = []
    
    # 日本語優先、漫画・書籍で検索
    params = {
        "q": query,
        "maxResults": 10,
        "orderBy": "relevance",
        "langRestrict": "ja",
        "printType": "books"
    }
    
    try:
        response = requests.get(base_url, params=params)
        data = response.json()
        
        if "items" in data:
            for item in data["items"]:
                info = item.get("volumeInfo", {})
                
                # 必要な情報を抽出
                title = info.get("title", "")
                authors = info.get("authors", ["不明"])
                image_links = info.get("imageLinks", {})
                thumbnail = image_links.get("thumbnail", "")
                
                # 重複排除用のキー
                if title:
                    results.append({
                        "title": title,
                        "author": ", ".join(authors),
                        "thumbnail": thumbnail
                    })

        return results
        
    except Exception as e:
        return []

def fetch_next_release_date(title, current_volume):
    """次回作発売日検索"""
    next_vol = int(current_volume) + 1
    base_url = "https://www.googleapis.com/books/v1/volumes"
    
    # 検索クエリ: "タイトル" 巻数
    params = {
        "q": f'"{title}" {next_vol}',
        "orderBy": "newest",
        "langRestrict": "ja"
    }

    try:
        response = requests.get(base_url, params=params)
        data = response.json()
        if "items" in data:
            for item in data["items"]:
                info = item.get("volumeInfo", {})
                # タイトルが部分一致するか確認
                if title in info.get("title", ""):
                    return info.get("publishedDate")
    except:
        return None
    return None

# --- アプリケーション本体 ---

st.set_page_config(page_title="漫画管理アプリ", layout="wide")
st.title("📚 漫画管理アプリ")

# セッションステートの初期化
if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()

# 検索結果の状態管理
if 'search_results' not in st.session_state:
    st.session_state.search_results = []
if 'selected_book' not in st.session_state:
    st.session_state.selected_book = None

# --- 1. 漫画登録セクション ---
st.header("漫画登録")

# --- A. タイトル検索エリア ---
with st.container():
    st.info("💡 漫画名を入力して「検索」してください。候補と表紙が表示されます。")
    col_search_input, col_search_btn = st.columns([3, 1])
    
    with col_search_input:
        search_query = st.text_input("漫画名検索", placeholder="例: 呪術廻戦、ワンピース", key="search_input")
    
    with col_search_btn:
        st.write("") 
        st.write("")
        search_clicked = st.button("🔍 検索", type="primary")

    # 検索実行
    if search_clicked and search_query:
        with st.spinner('本を探しています...'):
            results = search_books_api(search_query)
            st.session_state.search_results = results
            st.session_state.selected_book = None # 検索し直したら選択リセット
            if not results:
                st.warning("候補が見つかりませんでした。")

    # 検索結果の表示（リッチな表示に変更）
    if st.session_state.search_results:
        # 選択肢のラベルを作成（タイトル + 著者）
        options = ["(選択してください)"] + [f"{r['title']} ({r['author']})" for r in st.session_state.search_results]
        
        selected_option = st.selectbox(
            "↓ 候補から選択してください", 
            options,
            key="search_select"
        )
        
        # 選択されたら詳細データを保持
        if selected_option and selected_option != "(選択してください)":
            index = options.index(selected_option) - 1
            st.session_state.selected_book = st.session_state.search_results[index]

# --- B. 詳細入力フォーム ---
# 選択された本の情報があれば初期値にする
initial_title = ""
initial_image = ""

if st.session_state.selected_book:
    initial_title = st.session_state.selected_book['title']
    initial_image = st.session_state.selected_book['thumbnail']

# フォームエリア
with st.form("register_form", clear_on_submit=False):
    st.markdown("#### 📝 登録内容")
    
    # 2カラムレイアウト：左に入力、右に表紙画像
    col_form, col_img = st.columns([2, 1])
    
    with col_form:
        input_title = st.text_input("タイトル", value=initial_title)
        input_volume = st.number_input("最新の所持巻数", min_value=1, step=1, value=1)
        input_status = st.selectbox("状態", ["own", "want"], format_func=lambda x: "持ってる" if x == "own" else "欲しい")
        input_date = st.text_input("次巻発売日 (空欄で自動取得)", placeholder="YYYY-MM-DD")

    with col_img:
        if initial_image:
            st.image(initial_image, caption="表紙イメージ", width=100)
        else:
            st.write("No Image")

    submitted = st.form_submit_button("リストに追加する")

    if submitted:
        if not input_title:
            st.error("タイトルを入力してください。")
        else:
            # 発売日自動取得
            if not input_date:
                with st.spinner(f'『{input_title}』の次巻情報を検索中...'):
                    fetched_date = fetch_next_release_date(input_title, input_volume)
                    if fetched_date:
                        input_date = fetched_date
                        st.success(f"発売日が見つかりました: {fetched_date}")
                    else:
                        input_date = "不明"
                        st.warning("発売日が見つかりませんでした")

            new_entry = {
                "id": datetime.now().strftime("%Y%m%d%H%M%S"),
                "title": input_title,
                "volume": input_volume,
                "releaseDate": input_date,
                "status": input_status,
                "image": initial_image # 画像URLも保存
            }
            
            st.session_state.manga_data.append(new_entry)
            save_data(st.session_state.manga_data)
            
            st.success(f"『{input_title}』を追加しました！")
            
            # リセット
            st.session_state.search_results = []
            st.session_state.selected_book = None
            st.rerun()

st.divider()

# --- 2. リスト表示・編集セクション ---
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
else:
    df = pd.DataFrame(columns=["id", "title", "volume", "releaseDate", "status", "image"])

tab1, tab2 = st.tabs(["📘 持ってる漫画", "🌟 欲しい漫画"])

# 画像列を表示するための設定（Streamlit 1.23+）
column_config = {
    "image": st.column_config.ImageColumn("表紙", width="small"),
    "title": "タイトル",
    "volume": st.column_config.NumberColumn("最新巻数", format="%d巻"),
    "releaseDate": st.column_config.DateColumn("次巻発売日", format="YYYY-MM-DD"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"], required=True),
    "id": None
}

def update_data(edited_df, original_status):
    updated_list = edited_df.to_dict(orient="records")
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
    # CSVには画像URLを含めるか、除外するか（ここでは含める）
    csv_df = pd.DataFrame(st.session_state.manga_data).drop(columns=['id'])
    csv = csv_df.to_csv(index=False, encoding='utf_8_sig')
    st.download_button("📥 CSVダウンロード", csv, "manga_list.csv", "text/csv")
