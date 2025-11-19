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

def fetch_next_release_date(title, current_volume):
    """
    Google Books APIを使って次巻の発売日を検索する簡易関数
    完全な精度ではありませんが、タイトルと次巻数で検索をかけます。
    """
    next_vol = int(current_volume) + 1
    query = f"{title} {next_vol}"
    url = f"https://www.googleapis.com/books/v1/volumes?q={query}&orderBy=newest"
    
    try:
        response = requests.get(url)
        data = response.json()
        if "items" in data:
            # 最も関連性の高い検索結果の出版日を取得
            book_info = data["items"][0]["volumeInfo"]
            if "publishedDate" in book_info:
                return book_info["publishedDate"]
    except Exception as e:
        return None
    return None

# --- アプリケーション本体 ---

st.set_page_config(page_title="漫画管理アプリ", layout="wide")
st.title("📚 漫画管理アプリ")

# セッションステートの初期化
if 'manga_data' not in st.session_state:
    st.session_state.manga_data = load_data()

# --- 1. 漫画登録セクション ---
st.header("漫画登録")

with st.form("register_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    with col1:
        input_title = st.text_input("タイトル", placeholder="例: ONE PIECE")
        input_volume = st.number_input("最新の所持巻数", min_value=1, step=1)
    
    with col2:
        # ステータス選択
        input_status = st.selectbox("状態", ["own", "want"], format_func=lambda x: "持ってる" if x == "own" else "欲しい")
        # 発売日は手動でも入力できるが、空欄なら自動取得を試みる
        input_date = st.text_input("次巻発売日 (空欄で自動取得)", placeholder="YYYY-MM-DD")

    submitted = st.form_submit_button("登録する")

    if submitted and input_title:
        # 発売日が空欄の場合、APIで取得を試みる
        if not input_date:
            with st.spinner(f'『{input_title}』 {input_volume + 1}巻の発売日を検索中...'):
                fetched_date = fetch_next_release_date(input_title, input_volume)
                if fetched_date:
                    input_date = fetched_date
                    st.success(f"発売日が見つかりました: {fetched_date}")
                else:
                    input_date = "不明"
                    st.warning("発売日が見つかりませんでした。")

        # 新規データ作成
        new_entry = {
            "id": datetime.now().strftime("%Y%m%d%H%M%S"), # 一意なID
            "title": input_title,
            "volume": input_volume,
            "releaseDate": input_date,
            "status": input_status
        }
        
        st.session_state.manga_data.append(new_entry)
        save_data(st.session_state.manga_data)
        st.rerun()

st.divider()

# --- 2. リスト表示・編集セクション ---
# データフレームの作成
if st.session_state.manga_data:
    df = pd.DataFrame(st.session_state.manga_data)
else:
    df = pd.DataFrame(columns=["id", "title", "volume", "releaseDate", "status"])

# タブで表示を切り替え
tab1, tab2 = st.tabs(["📘 持ってる漫画", "🌟 欲しい漫画"])

# 共通の編集用設定
column_config = {
    "title": "タイトル",
    "volume": st.column_config.NumberColumn("最新巻数", format="%d巻"),
    "releaseDate": st.column_config.DateColumn("次巻発売日", format="YYYY-MM-DD"),
    "status": st.column_config.SelectboxColumn("状態", options=["own", "want"], required=True),
    "id": None # ID列は隠す
}

def update_data(edited_df, original_status):
    """編集されたデータフレームを元のリストに反映して保存する"""
    # 編集後のデータを辞書リストに変換
    updated_list = edited_df.to_dict(orient="records")
    
    # 現在表示していないステータスのデータ（バックグラウンドにあるデータ）を保持
    other_status_data = [d for d in st.session_state.manga_data if d['status'] != original_status]
    
    # 結合して保存
    final_data = other_status_data + updated_list
    st.session_state.manga_data = final_data
    save_data(final_data)

# --- Tab 1: 持ってる漫画 ---
with tab1:
    df_own = df[df['status'] == 'own']
    if not df_own.empty:
        st.caption("表をダブルクリックで編集できます。行を選択してDeleteキーで削除できます。")
        edited_df_own = st.data_editor(
            df_own,
            column_config=column_config,
            num_rows="dynamic", # 行の追加・削除を許可
            use_container_width=True,
            key="editor_own",
            hide_index=True
        )
        
        # 変更があった場合のみ保存処理（Streamlitの仕様上、rerunでデータがリセットされないように即時反映）
        if not df_own.equals(edited_df_own):
            update_data(edited_df_own, "own")
            st.rerun()
    else:
        st.info("登録された漫画はありません。")

# --- Tab 2: 欲しい漫画 ---
with tab2:
    df_want = df[df['status'] == 'want']
    if not df_want.empty:
        st.caption("表をダブルクリックで編集できます。")
        edited_df_want = st.data_editor(
            df_want,
            column_config=column_config,
            num_rows="dynamic",
            use_container_width=True,
            key="editor_want",
            hide_index=True
        )
        
        if not df_want.equals(edited_df_want):
            update_data(edited_df_want, "want")
            st.rerun()
    else:
        st.info("欲しい漫画はありません。")

# --- 3. CSVダウンロード ---
st.divider()

# 全データをCSV用に変換
if st.session_state.manga_data:
    csv_df = pd.DataFrame(st.session_state.manga_data).drop(columns=['id']) # IDは出力しない
    csv = csv_df.to_csv(index=False, encoding='utf_8_sig') # Excelで文字化けしないようBOM付きUTF-8

    st.download_button(
        label="📥 CSVファイルとしてダウンロード",
        data=csv,
        file_name='manga_list.csv',
        mime='text/csv',
    )
