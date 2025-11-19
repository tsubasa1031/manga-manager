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
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_data(data):
    """データをJSONファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def search_books_api(query):
    """Google Books APIで本を検索し、候補リストを返す"""
    if not query:
        return []
    
    url = f"https://www.googleapis.com/books/v1/volumes?q=intitle:{query}&maxResults=5&orderBy=relevance"
    try:
        response = requests.get(url)
        data = response.json()
        candidates = []
        if "items" in data:
            for item in data["items"]:
                info = item.get("volumeInfo", {})
                title = info.get("title", "不明")
                # 発売日などの付加情報も取っておく（今回はタイトルのみ使用）
                candidates.append(title)
        return list(set(candidates)) # 重複排除
    except:
        return []

def fetch_next_release_date(title, current_volume):
    """次回作発売日検索（既存機能）"""
    next_vol = int(current_volume) + 1
    query = f"{title} {next_vol}"
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&orderBy=newest"
    try:
        response = requests.get(url)
        data = response.json()
        if "items" in data:
            book_info = data["items"][0]["volumeInfo"]
            if "publishedDate" in book_info:
                return book_info["publishedDate"]
    except:
        return None
    return None

# --- アプリケーション本体 ---

st.set_page_config(page_title="漫画管理アプリ", layout="wide")
st.title("📚 漫画管理アプリ")

# セッションステートの初期化
if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()

# 選択されたタイトルを保持する変数
if 'selected_title_candidate' not in st.session_state:
    st.session_state.selected_title_candidate = ""

# --- 1. 漫画登録セクション ---
st.header("漫画登録")

# --- A. タイトル検索エリア（予測変換風） ---
with st.container():
    st.markdown("##### 🔍 タイトル検索")
    col_search, col_result = st.columns([1, 2])
    
    with col_search:
        # ユーザーが途中まで入力する場所
        search_query = st.text_input("漫画名の一部を入力", placeholder="例: ワンピ")
    
    with col_result:
        # 検索文字がある場合のみAPIを叩く
        if search_query:
            candidates = search_books_api(search_query)
            if candidates:
                # 候補が見つかったらセレクトボックスで選ばせる
                selected = st.selectbox("候補から選択してください:", candidates, key="search_select")
                if selected:
                    st.session_state.selected_title_candidate = selected
            else:
                st.warning("候補が見つかりませんでした。")

# --- B. 詳細入力フォーム ---
# 検索で選んだタイトルがあれば、それを初期値にする
initial_title = st.session_state.get('selected_title_candidate', "")

with st.form("register_form", clear_on_submit=False): # フォーム内での値保持のためclear_on_submitはFalse推奨
    col1, col2 = st.columns(2)
    with col1:
        # 検索結果をvalueにセット
        input_title = st.text_input("タイトル（確定）", value=initial_title)
        input_volume = st.number_input("最新の所持巻数", min_value=1, step=1, value=1)
    
    with col2:
        input_status = st.selectbox("状態", ["own", "want"], format_func=lambda x: "持ってる" if x == "own" else "欲しい")
        input_date = st.text_input("次巻発売日 (空欄で自動取得)", placeholder="YYYY-MM-DD")

    submitted = st.form_submit_button("リストに追加する")

    if submitted and input_title:
        # 発売日自動取得ロジック
        if not input_date:
            with st.spinner(f'『{input_title}』の次巻情報を検索中...'):
                fetched_date = fetch_next_release_date(input_title, input_volume)
                if fetched_date:
                    input_date = fetched_date
                    st.success(f"発売日が見つかりました: {fetched_date}")
                else:
                    input_date = "不明"
                    st.warning("発売日が見つかりませんでした。")

        # データ保存
        new_entry = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S"),
            "title": input_title,
            "volume": input_volume,
            "releaseDate": input_date,
            "status": input_status
        }
        
        st.session_state.manga_data.append(new_entry)
        save_data(st.session_state.manga_data)
        
        # 完了後のクリア処理
        st.session_state.selected_title_candidate = "" 
        st.success(f"『{input_title}』を追加しました！")
        
        # 画面更新して入力をリセット
        # time.sleep(1) # 連続投稿を防ぐなら入れても良い
        st.rerun()

st.divider()

# --- 2. リスト表示・編集セクション ---
# (ここは前回のコードと同じなので、そのまま機能します)
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
else:
    df = pd.DataFrame(columns=["id", "title", "volume", "releaseDate", "status"])

tab1, tab2 = st.tabs(["📘 持ってる漫画", "🌟 欲しい漫画"])

column_config = {
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
    csv_df = pd.DataFrame(st.session_state.manga_data).drop(columns=['id'])
    csv = csv_df.to_csv(index=False, encoding='utf_8_sig')
    st.download_button("📥 CSVダウンロード", csv, "manga_list.csv", "text/csv")
