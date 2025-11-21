import streamlit as st
import pandas as pd
import json
import os
import requests
import xml.etree.ElementTree as ET
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

# --- 国立国会図書館 (NDL) API 関連関数 ---

def get_text_from_element(element, tag, namespaces):
    """XML要素からテキストを取得するヘルパー関数"""
    found = element.find(tag, namespaces)
    return found.text if found is not None else ""

def search_ndl(query, media_type='1'):
    """
    国立国会図書館サーチ OpenSearch API で検索
    Endpoint: https://ndlsearch.ndl.go.jp/api/opensearch
    
    media_type:
        '1': 本 (Books) -> 漫画はここ
        '9': 映像 (Video) -> アニメはここ
        '': すべて
    """
    if not query: return []
    
    url = "https://ndlsearch.ndl.go.jp/api/opensearch"
    # cnt: 取得件数
    params = {
        "title": query,
        "cnt": 20,
    }
    
    # メディアタイプ指定がある場合のみ追加
    if media_type:
        params["mediatype"] = media_type
    
    results = []
    try:
        response = requests.get(url, params=params)
        # XMLをパース
        root = ET.fromstring(response.content)
        
        # 名前空間の定義 (RSS 2.0 + DC)
        namespaces = {
            'dc': 'http://purl.org/dc/elements/1.1/',
            'openSearch': 'http://a9.com/-/spec/opensearchrss/1.0/',
            'rdfs': 'http://www.w3.org/2000/01/rdf-schema#'
        }
        
        # channel要素の下にあるitem要素をループ
        for item in root.findall('.//item'):
            title = get_text_from_element(item, 'title', namespaces)
            author = get_text_from_element(item, 'author', namespaces) # item直下のauthorはRSS標準
            if not author:
                author = get_text_from_element(item, 'dc:creator', namespaces) # なければdc:creator
                
            publisher = get_text_from_element(item, 'dc:publisher', namespaces)
            link = get_text_from_element(item, 'link', namespaces)
            
            # ISBNの取得
            isbn = ""
            for ident in item.findall('dc:identifier', namespaces):
                val = ident.text.replace('-', '') if ident.text else ""
                if val.isdigit() and (len(val) == 13 or len(val) == 10):
                    isbn = val
                    break
            
            # タイトルがあり、かつ重複していない場合に追加
            if title and not any(r['title'] == title for r in results):
                # 書影URLの生成 (NDL書影API)
                thumbnail = ""
                if isbn:
                    thumbnail = f"https://ndlsearch.ndl.go.jp/thumbnail/{isbn}.jpg"
                
                results.append({
                    "title": title,
                    "author": author,
                    "publisher": publisher,
                    "thumbnail": thumbnail,
                    "link": link,
                    "isbn": isbn,
                    "source": "NDL" # 国立国会図書館
                })
                
        return results
    except Exception as e:
        return []

def fetch_date_ndl(title, next_vol):
    """
    国立国会図書館サーチ API で次巻の発売日を検索
    dpid=jpro (JPRO) を指定して出版予定・新刊情報を優先検索
    """
    url = "https://ndlsearch.ndl.go.jp/api/opensearch"
    query = f"{title} {next_vol}"
    
    # dpid=jpro を指定して出版情報(近刊含む)を狙う
    params = {
        "title": query,
        "cnt": 1,
        "dpid": "jpro" 
    }
    
    try:
        response = requests.get(url, params=params)
        root = ET.fromstring(response.content)
        namespaces = {'dc': 'http://purl.org/dc/elements/1.1/'}
        
        # 最初のitemのdc:dateを取得
        item = root.find('.//item')
        if item is not None:
            date_str = get_text_from_element(item, 'dc:date', namespaces)
            if not date_str:
                 date_str = get_text_from_element(item, 'pubDate', namespaces)
            return date_str
            
    except:
        pass
        
    # jproで見つからなければ通常検索で再トライ
    if "dpid" in params:
        del params["dpid"]
        try:
            response = requests.get(url, params=params)
            root = ET.fromstring(response.content)
            namespaces = {'dc': 'http://purl.org/dc/elements/1.1/'}
            item = root.find('.//item')
            if item is not None:
                return get_text_from_element(item, 'dc:date', namespaces)
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
    st.caption("Data Source: 国立国会図書館サーチ (NDL Search)")

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
    
    # --- 検索エリア ---
    with st.container():
        # タイトルと検索ボタンのカラム
        col_s1, col_s2 = st.columns([3, 1])
        
        with col_s1:
            search_query = st.text_input("タイトル検索 (NDL)", placeholder="例: 呪術廻戦", key="s_in")
            
            # フィルタ選択（ラジオボタン）を追加
            # 1=本(漫画), 9=映像(アニメ), ''=すべて
            filter_label = st.radio(
                "検索フィルタ:",
                ["漫画・書籍 (Books)", "アニメ・映像 (Video)", "すべて"],
                index=0,
                horizontal=True,
                key="search_filter_radio"
            )
            
            # 選択肢をAPIパラメータに変換
            if "漫画" in filter_label:
                media_type_code = '1'
            elif "アニメ" in filter_label:
                media_type_code = '9'
            else:
                media_type_code = ''

        with col_s2:
            st.write("")
            st.write("")
            # 検索ボタン
            search_clicked = st.button("🔍 検索", type="primary")

        if search_clicked and search_query:
            with st.spinner('国立国会図書館サーチで検索中...'):
                st.session_state.selected_book = None
                # NDL一本で検索（フィルタ適用）
                results = search_ndl(search_query, media_type=media_type_code)
                st.session_state.search_results = results
                if not results: st.warning("見つかりませんでした。")

        if st.session_state.search_results:
            opts = ["(選択してください)"] + [f"{r['title']} - {r['author']}" for r in st.session_state.search_results]
            sel = st.selectbox("候補を選択", opts, key="s_sel")
            if sel != "(選択してください)":
                st.session_state.selected_book = st.session_state.search_results[opts.index(sel)-1]

    # --- 入力フォーム ---
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
            date = r5.text_input("次巻発売日", placeholder="YYYY-MM-DD")
            
            r6, r7 = st.columns(2)
            f_chk = r6.checkbox("完結済み")
            u_chk = r7.checkbox("未読")
            
            st.caption(f"著者: {init['author']} / 出版社: {init['publisher']}")

        with c2:
            if init["image"]: st.image(init["image"], width=100)
            else: st.info("No Image")

        if st.form_submit_button("追加") and title:
            # 発売日自動取得 (NDL)
            if not date:
                next_v = vol + 1
                fetched = fetch_date_ndl(title, next_v)
                if fetched: 
                    date = fetched
                    st.success(f"発売日発見: {fetched}")
                else:
                    st.warning("発売日が見つかりませんでした。")

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


# --- ビュー定義 (全件リスト等) ---
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
