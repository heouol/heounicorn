import streamlit as st
import streamlit_authenticator as stauth  # Добавляем для аутентификации
import yaml  # Добавляем для работы с config.yaml
from yaml.loader import SafeLoader  # Добавляем для загрузки YAML
import requests
from bs4 import BeautifulSoup
import pandas as pd
from collections import defaultdict
import time
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime, timedelta
import json
import os

# Set page config at the start (must be the first Streamlit command)
st.set_page_config(layout="wide", page_title="PRM Analytics")

# Global constants for SoloQ
SUMMONER_NAME_BY_URL = "https://europe.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{}/{}?api_key=RGAPI-2364bf09-8116-4d02-9dde-e2ed7cde4af8"
MATCH_HISTORY_URL = "https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{}/ids?start=0&count=100&api_key=RGAPI-2364bf09-8116-4d02-9dde-e2ed7cde4af8"
MATCH_BASIC_URL = "https://europe.api.riotgames.com/lol/match/v5/matches/{}?api_key=RGAPI-2364bf09-8116-4d02-9dde-e2ed7cde4af8"

# Список URL для разных этапов турнира
TOURNAMENT_URLS = {
    "Spring Split": {
        "match_history": "https://lol.fandom.com/wiki/Prime_League_1st_Division/2025_Season/Spring_Split/Match_History",
        "picks_and_bans": "https://lol.fandom.com/wiki/Prime_League_1st_Division/2025_Season/Spring_Split/Picks_and_Bans"
    }
}

# Team roster for UOL SE
team_rosters = {
    "Unicorns of Love Sexy Edition": {
        "Fornoreason": {"game_name": ["床前明月光疑是地上霜举头望明月低", "FornoReason"], "tag_line": ["CN1", "Gap"], "role": "TOP"},
        "White": {"game_name": ["Alsabr"], "tag_line": ["314"], "role": "JUNGLE"},
        "Simpli": {"game_name": ["Simpli"], "tag_line": ["Jasmi"], "role": "MIDDLE"},
        "DenVoksne": {"game_name": ["Ignacarious", "Mαster Oogwαy"], "tag_line": ["5232", "EUW"], "role": "BOTTOM"},
        "seaz": {"game_name": ["고군분투일취월장", "ASV13 08"], "tag_line": ["KR6", "1130"], "role": "UTILITY"},
    }
}

# Get the latest patch version from Data Dragon
def get_latest_patch_version():
    try:
        response = requests.get("https://ddragon.leagueoflegends.com/api/versions.json")
        if response.status_code == 200:
            versions = response.json()
            return versions[0]
        return "14.5.1"
    except:
        return "14.5.1"

PATCH_VERSION = get_latest_patch_version()

# Normalize team names
def normalize_team_name(team_name):
    if not team_name or team_name.lower() == "unknown blue" or team_name.lower() == "unknown red":
        return "unknown"
    
    team_exceptions = {
        "dung dynasty": "Dung Dynasty",
        "dung dynastylogo std": "Dung Dynasty",
        "dnd": "Dung Dynasty",
        "eintracht spandau": "Eintracht Spandau",
        "eintracht spandaulogo std": "Eintracht Spandau",
        "eins": "Eintracht Spandau",
        "rossmann centaurs": "ROSSMANN Centaurs",
        "rossmann centaurslogo std": "ROSSMANN Centaurs",
        "ross": "ROSSMANN Centaurs",
        "unicorns of love sexy edition": "Unicorns of Love Sexy Edition",
        "use": "Unicorns of Love Sexy Edition",
        "unicorns of love sexy editionlogo std": "Unicorns of Love Sexy Edition",
        "kaufland hangry knights": "Kaufland Hangry Knights",
        "kaufland hangry knightslogo std": "Kaufland Hangry Knights",
        "khk": "Kaufland Hangry Knights",
        "berlin international gaming": "Berlin International Gaming",
        "big": "Berlin International Gaming",
        "berlin international gaminglogo std": "Berlin International Gaming",
        "eintracht frankfurt": "Eintracht Frankfurt",
        "eintracht frankfurtlogo std": "Eintracht Frankfurt",
        "sge": "Eintracht Frankfurt",
        "austrian force willhaben": "Austrian Force willhaben",
        "afw": "Austrian Force willhaben",
        "tog": "teamorangegaming",
        "e wie einfach e-sports": "E Wie Einfach E-sports",
        "ewi": "E Wie Einfach E-sports"
    }

    team_name_clean = team_name.lower().replace("logo std", "").strip()
    
    for key, normalized_name in team_exceptions.items():
        if team_name_clean == key or key in team_name_clean:
            return normalized_name
    
    return team_name_clean

# Fetch match history data
def get_champion_from_title(span_tag):
    if span_tag and 'title' in span_tag.attrs:
        return span_tag['title']
    return "N/A"

# Helper function to safely get team name from complex cell structure in MH table
def get_team_name_from_mh_cell(cell):
    # Try finding 'a' tag with title directly within the cell
    link = cell.select_one('a[title]')
    if link and link.get('title'):
        return link['title']
    # Try finding img tag and getting title from its parent 'a' tag
    img = cell.select_one('img')
    if img:
        parent_link = img.find_parent('a')
        if parent_link and parent_link.get('title'):
            return parent_link['title']
    # Fallback to cell text if nothing else found
    cleaned_text = cell.text.strip().replace("⁠", "") # Удаляем невидимые символы
    return cleaned_text if cleaned_text else "unknown"


# Fetch match history data for role-based stats and opponent bans
def fetch_match_history_data():
    print("Fetching Match History Data (for Role/Duo Stats & Opponent Bans)...")
    team_data = defaultdict(lambda: {
        'Top': defaultdict(lambda: {'games': 0, 'wins': 0}),
        'Jungle': defaultdict(lambda: {'games': 0, 'wins': 0}),
        'Mid': defaultdict(lambda: {'games': 0, 'wins': 0}),
        'ADC': defaultdict(lambda: {'games': 0, 'wins': 0}),
        'Support': defaultdict(lambda: {'games': 0, 'wins': 0}),
        # 'Bans': defaultdict(int), # Баны самой команды здесь не нужны, берем из draft_data
        'OpponentBlueBansFirst3': defaultdict(int), # Первые 3 бана оппонента, когда эта команда играла СИНЕЙ
        'OpponentRedBansFirst3': defaultdict(int),  # Первые 3 бана оппонента, когда эта команда играла КРАСНОЙ
        'DuoPicks': defaultdict(lambda: {'games': 0, 'wins': 0}),
        'MatchResults': []
    })
    roles = ['Top', 'Jungle', 'Mid', 'ADC', 'Support'] # Предполагаемый порядок ролей в MH таблице

    for tournament_name, urls in TOURNAMENT_URLS.items():
        url = urls["match_history"]
        print(f"Fetching MH from: {url}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            st.error(f"MH Fetch Error for {tournament_name}: {e}")
            continue

        soup = BeautifulSoup(response.content, 'html.parser')
        match_history_tables = soup.select('.wikitable.mhgame.sortable')
        if not match_history_tables:
            st.warning(f"No MH tables found for {tournament_name}")
            continue
        print(f"Found {len(match_history_tables)} MH tables for {tournament_name}.")

        for table_index, match_history_table in enumerate(match_history_tables):
            print(f"Processing MH table {table_index + 1}...")
            rows = match_history_table.select('tr')
            print(f"Found {len(rows) - 1} MH rows.")

            for i, row in enumerate(rows[1:]):
                cols = row.select('td')
                # Индексы: 0:Date, 1:Patch, 2:Blue, 3:Red, 4:Winner, 5:BBans, 6:RBans, 7:BPicks, 8:RPicks
                if len(cols) < 9:
                    print(f"Skipping MH row {i+1}, cols={len(cols)} < 9")
                    continue

                try:
                    blue_team_raw = get_team_name_from_mh_cell(cols[2])
                    red_team_raw = get_team_name_from_mh_cell(cols[3])
                    winner_raw = get_team_name_from_mh_cell(cols[4])

                    blue_team = normalize_team_name(blue_team_raw)
                    red_team = normalize_team_name(red_team_raw)
                    winner_team = normalize_team_name(winner_raw)

                    if blue_team == "unknown" or red_team == "unknown":
                        print(f"Skipping MH row {i+1}: Unknown team (Raw Blue: '{blue_team_raw}', Raw Red: '{red_team_raw}')")
                        continue

                    print(f"MH Row {i+1}: Blue='{blue_team}' Red='{red_team}' Winner='{winner_team}'")

                    result_blue = 'Win' if winner_team == blue_team else 'Loss'
                    result_red = 'Win' if winner_team == red_team else 'Loss'
                    if winner_team == "unknown": result_blue = result_red = 'Loss'

                    # Баны (используем .sprite.champion-sprite)
                    blue_ban_spans = cols[5].select('span.sprite.champion-sprite')
                    red_ban_spans = cols[6].select('span.sprite.champion-sprite')
                    # Берем только первые 3 для статистики банов оппонента
                    blue_bans_first3 = [get_champion_from_title(ban) for ban in blue_ban_spans[:3] if get_champion_from_title(ban) != "N/A"]
                    red_bans_first3 = [get_champion_from_title(ban) for ban in red_ban_spans[:3] if get_champion_from_title(ban) != "N/A"]

                    # Пики (предполагаем порядок ролей Top->Sup)
                    blue_pick_spans = cols[7].select('span.sprite.champion-sprite')
                    red_pick_spans = cols[8].select('span.sprite.champion-sprite')
                    blue_picks_role_ordered = [get_champion_from_title(pick) for pick in blue_pick_spans]
                    red_picks_role_ordered = [get_champion_from_title(pick) for pick in red_pick_spans]

                    # Дополняем до 5 пиков, если нужно
                    while len(blue_picks_role_ordered) < 5: blue_picks_role_ordered.append("N/A")
                    while len(red_picks_role_ordered) < 5: red_picks_role_ordered.append("N/A")

                    # --- Обновление статистики ---
                    # Синяя команда
                    stats_blue = team_data[blue_team]
                    stats_blue['MatchResults'].append({'opponent': red_team, 'side': 'blue', 'win': result_blue == 'Win'})
                    for opp_ban in red_bans_first3: stats_blue['OpponentRedBansFirst3'][opp_ban] += 1 # Баны оппонента (красного)
                    blue_picks_map = {}
                    for role_idx, champ in enumerate(blue_picks_role_ordered[:5]):
                        role = roles[role_idx]
                        stats_blue[role][champ]['games'] += 1 # Увеличиваем счетчик игр для N/A тоже
                        if result_blue == 'Win': stats_blue[role][champ]['wins'] += 1
                        if champ != "N/A": blue_picks_map[role] = champ

                    # Красная команда
                    stats_red = team_data[red_team]
                    stats_red['MatchResults'].append({'opponent': blue_team, 'side': 'red', 'win': result_red == 'Win'})
                    for opp_ban in blue_bans_first3: stats_red['OpponentBlueBansFirst3'][opp_ban] += 1 # Баны оппонента (синего)
                    red_picks_map = {}
                    for role_idx, champ in enumerate(red_picks_role_ordered[:5]):
                        role = roles[role_idx]
                        stats_red[role][champ]['games'] += 1
                        if result_red == 'Win': stats_red[role][champ]['wins'] += 1
                        if champ != "N/A": red_picks_map[role] = champ

                    # Дуо-пики
                    duo_pairs = [('Top', 'Jungle'), ('Jungle', 'Mid'), ('Jungle', 'Support'), ('ADC', 'Support')]
                    for r1, r2 in duo_pairs:
                        # Blue Duo
                        c1_b, c2_b = blue_picks_map.get(r1), blue_picks_map.get(r2)
                        if c1_b and c2_b:
                            key_b = tuple(sorted((c1_b, c2_b))) + tuple(sorted((r1, r2)))
                            stats_blue['DuoPicks'][key_b]['games'] += 1
                            if result_blue == 'Win': stats_blue['DuoPicks'][key_b]['wins'] += 1
                        # Red Duo
                        c1_r, c2_r = red_picks_map.get(r1), red_picks_map.get(r2)
                        if c1_r and c2_r:
                            key_r = tuple(sorted((c1_r, c2_r))) + tuple(sorted((r1, r2)))
                            stats_red['DuoPicks'][key_r]['games'] += 1
                            if result_red == 'Win': stats_red['DuoPicks'][key_r]['wins'] += 1

                except Exception as e:
                    st.error(f"Error processing MH row {i+1} in table {table_index+1}: {e}")
                    print(f"MH Error details: Row index {i+1}, Table index {table_index+1}, URL: {url}")

    print("Finished fetching Match History Data.")
    return dict(team_data)
# Fetch first bans data

# Fetch draft data
def get_champion_from_span(span_tag):
    if span_tag:
        # Попробуем извлечь из вложенного span с title
        nested_span = span_tag.select_one('.sprite.champion-sprite')
        if nested_span and 'title' in nested_span.attrs:
            return nested_span['title']
        # Попробуем извлечь из data-champion родительского span
        if 'data-champion' in span_tag.attrs:
            return span_tag.get('data-champion')
        # Попробуем извлечь из title самого span (если нет вложенного)
        if 'title' in span_tag.attrs:
             return span_tag['title']
        # Если ничего не найдено в pbh-cn, поищем простой champion-sprite
        simple_sprite = span_tag.find_parent('td').select_one('span.champion-sprite')
        if simple_sprite and 'title' in simple_sprite.attrs:
            return simple_sprite['title']
    return "N/A"

# Fetch draft data
def get_champion_from_draft_cell(cell):
    if not cell: return "N/A"
    # Try the specific structure first: td -> span.pbh-cn -> span.sprite[title]
    pbh_cn = cell.select_one('span.pbh-cn')
    if pbh_cn:
        sprite = pbh_cn.select_one('span.sprite.champion-sprite')
        if sprite and 'title' in sprite.attrs:
            return sprite['title']
        # Fallback to data-champion on pbh-cn if title is missing
        if 'data-champion' in pbh_cn.attrs:
             champ_name = pbh_cn['data-champion']
             # Basic normalization example:
             if champ_name.lower() == 'monkeyking': return 'Wukong'
             if champ_name.lower() == 'jarvaniv': return 'Jarvan IV'
             if champ_name.lower() == 'kaisa': return "Kai'Sa"
             # Add more normalizations if needed based on data-champion values
             # Heuristic: Capitalize except for apostrophes or known multi-word names
             if "'" in champ_name or " " in champ_name: # Basic check
                 return champ_name # Assume it's already correct
             else:
                 return champ_name.capitalize()
    # Fallback for simpler structures (like maybe bans in some old formats)
    sprite = cell.select_one('span.sprite.champion-sprite')
    if sprite and 'title' in sprite.attrs:
        return sprite['title']
    return "N/A"

# Helper to get champion from potentially multiple spans in a cell (for P/B table)
def get_champions_from_draft_pick_cell(cell):
    champions = []
    if not cell: return ["N/A"]
    spans = cell.select('span.pbh-cn')
    if spans:
        for span in spans:
             sprite = span.select_one('span.sprite.champion-sprite')
             if sprite and 'title' in sprite.attrs:
                 champions.append(sprite['title'])
             elif 'data-champion' in span.attrs:
                  champ_name = span['data-champion']
                  # Add normalization if needed
                  if champ_name.lower() == 'monkeyking': champ_name = 'Wukong'
                  elif champ_name.lower() == 'jarvaniv': champ_name = 'Jarvan IV'
                  elif champ_name.lower() == 'kaisa': champ_name = "Kai'Sa"
                  elif "'" not in champ_name and " " not in champ_name: champ_name = champ_name.capitalize()
                  champions.append(champ_name)
             else:
                  champions.append("N/A")
    else: # Fallback if no pbh-cn spans
         sprite = cell.select_one('span.sprite.champion-sprite')
         if sprite and 'title' in sprite.attrs:
             champions.append(sprite['title'])

    return champions if champions else ["N/A"]


# Fetch draft data for visual draft display and team's first 3 bans
def fetch_draft_data():
    print("Fetching Draft (Picks/Bans Table) Data (for Draft Display & Team Bans)...")
    team_drafts = defaultdict(list)
    match_counter = defaultdict(int)
    team_wins_series = defaultdict(lambda: defaultdict(int))

    for tournament_name, urls in TOURNAMENT_URLS.items():
        url = urls["picks_and_bans"]
        print(f"Fetching P/B from: {url}")
        headers = {'User-Agent': 'Mozilla/5.0'}
        try:
            response = requests.get(url, headers=headers, timeout=20)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            st.error(f"P/B Fetch Error for {tournament_name}: {e}")
            continue

        soup = BeautifulSoup(response.content, 'html.parser')
        draft_tables = soup.select('table.wikitable.plainlinks.hoverable-rows.column-show-hide-1')
        if not draft_tables:
            st.warning(f"No P/B tables found for {tournament_name}")
            continue
        print(f"Found {len(draft_tables)} P/B tables for {tournament_name}.")

        for table_index, table in enumerate(draft_tables):
            print(f"Processing P/B table {table_index + 1}...")
            rows = table.select('tr')
            print(f"Found {len(rows) - 1} P/B rows.")

            for i, row in enumerate(rows[1:]):
                cols = row.select('td')
                 # Индексы: 0:Week 1:Blue 2:Red 3:Score 4:Patch 5:BB1 6:RB1 ... 10:RB3 11:BP1 12:RP1/2 13:BP2/3 14:RP3 15:RB4 16:BB4 17:RB5 18:BB5 19:RP4 20:BP4/5 21:RP5 22:SB 23:VOD?
                if len(cols) < 22:
                    print(f"Skipping P/B row {i+1}, cols={len(cols)} < 22")
                    continue

                try:
                    blue_team_raw = cols[1].get('title', cols[1].text).strip().replace("⁠", "")
                    red_team_raw = cols[2].get('title', cols[2].text).strip().replace("⁠", "")
                    blue_team = normalize_team_name(blue_team_raw)
                    red_team = normalize_team_name(red_team_raw)

                    if blue_team == "unknown" or red_team == "unknown":
                        print(f"Skipping P/B row {i+1}: Unknown team (Raw Blue: '{blue_team_raw}', Raw Red: '{red_team_raw}')")
                        continue

                    winner_side = None
                    if 'pbh-winner' in cols[1].get('class', []): winner_side = 'blue'
                    elif 'pbh-winner' in cols[2].get('class', []): winner_side = 'red'

                    print(f"P/B Row {i+1}: Blue='{blue_team}' Red='{red_team}' Winner Side='{winner_side}'")

                    match_key = tuple(sorted((blue_team, red_team)))
                    match_number = match_counter[match_key] + 1
                    match_counter[match_key] = match_number

                    current_blue_wins = team_wins_series[match_key]['blue']
                    current_red_wins = team_wins_series[match_key]['red']
                    if winner_side == 'blue': current_blue_wins += 1
                    elif winner_side == 'red': current_red_wins += 1
                    team_wins_series[match_key]['blue'] = current_blue_wins
                    team_wins_series[match_key]['red'] = current_red_wins

                    # --- Баны [B1, B2, B3, B4, B5] ---
                    blue_ban_indices = [5, 7, 9, 16, 18]
                    red_ban_indices = [6, 8, 10, 15, 17]
                    blue_bans = [get_champion_from_draft_cell(cols[idx]) for idx in blue_ban_indices]
                    red_bans = [get_champion_from_draft_cell(cols[idx]) for idx in red_ban_indices]

                    # --- Пики в визуальном порядке [P1, P2, P3, P4, P5] ---
                    blue_picks_ordered = ["N/A"] * 5
                    red_picks_ordered = ["N/A"] * 5

                    # BP1 (col 11)
                    bp1_champs = get_champions_from_draft_pick_cell(cols[11])
                    if bp1_champs: blue_picks_ordered[0] = bp1_champs[0]
                    # RP1, RP2 (col 12)
                    rp1_2_champs = get_champions_from_draft_pick_cell(cols[12])
                    if len(rp1_2_champs) > 0: red_picks_ordered[0] = rp1_2_champs[0]
                    if len(rp1_2_champs) > 1: red_picks_ordered[1] = rp1_2_champs[1]
                    # BP2, BP3 (col 13)
                    bp2_3_champs = get_champions_from_draft_pick_cell(cols[13])
                    if len(bp2_3_champs) > 0: blue_picks_ordered[1] = bp2_3_champs[0]
                    if len(bp2_3_champs) > 1: blue_picks_ordered[2] = bp2_3_champs[1]
                    # RP3 (col 14)
                    rp3_champs = get_champions_from_draft_pick_cell(cols[14])
                    if rp3_champs: red_picks_ordered[2] = rp3_champs[0]
                    # RP4 (col 19)
                    rp4_champs = get_champions_from_draft_pick_cell(cols[19])
                    if rp4_champs: red_picks_ordered[3] = rp4_champs[0]
                    # BP4, BP5 (col 20)
                    bp4_5_champs = get_champions_from_draft_pick_cell(cols[20])
                    if len(bp4_5_champs) > 0: blue_picks_ordered[3] = bp4_5_champs[0]
                    if len(bp4_5_champs) > 1: blue_picks_ordered[4] = bp4_5_champs[1]
                    # RP5 (col 21)
                    rp5_champs = get_champions_from_draft_pick_cell(cols[21])
                    if rp5_champs: red_picks_ordered[4] = rp5_champs[0]

                    # --- VOD ---
                    vod_link = "N/A"
                    # Проверяем, есть ли колонка VOD (индекс может меняться, часто последняя видимая)
                    vod_col_index = -1 # Ищем с конца
                    for col_idx in range(len(cols)-1, 21, -1):
                         link = cols[col_idx].select_one('a')
                         # Предполагаем, что VOD - это внешняя ссылка
                         if link and 'href' in link.attrs and not link['href'].startswith('/'):
                              vod_link = link['href']
                              break
                         # Иногда ссылка на Scoreboard в предпоследней колонке
                         elif link and 'href' in link.attrs and 'Scoreboards' in link['href'] and col_idx > 0:
                              # Проверяем предыдущую колонку на VOD
                               prev_link = cols[col_idx-1].select_one('a')
                               if prev_link and 'href' in prev_link.attrs and not prev_link['href'].startswith('/'):
                                   vod_link = prev_link['href']
                                   break


                    # --- Сохранение данных ---
                    draft_base = {
                        'winner_side': winner_side,
                        'blue_wins_series': current_blue_wins,
                        'red_wins_series': current_red_wins,
                        'match_key': match_key,
                        'match_number': match_number,
                        'vod_link': vod_link,
                        'tournament': tournament_name,
                        'absolute_blue_team': blue_team,
                        'absolute_red_team': red_team
                    }
                    # Для Blue Team
                    draft_blue = draft_base.copy()
                    draft_blue.update({
                        'opponent': red_team, 'side': 'blue',
                        'team_bans': blue_bans, 'opponent_bans': red_bans,
                        'team_picks_ordered': blue_picks_ordered,
                        'opponent_picks_ordered': red_picks_ordered,
                    })
                    team_drafts[blue_team].append(draft_blue)
                    # Для Red Team
                    draft_red = draft_base.copy()
                    draft_red.update({
                        'opponent': blue_team, 'side': 'red',
                        'team_bans': red_bans, 'opponent_bans': blue_bans,
                        'team_picks_ordered': red_picks_ordered,
                        'opponent_picks_ordered': blue_picks_ordered,
                    })
                    team_drafts[red_team].append(draft_red)

                except Exception as e:
                    st.error(f"Error processing P/B row {i+1} in table {table_index+1}: {e}")
                    print(f"P/B Error details: Row index {i+1}, Table index {table_index+1}, URL: {url}")

    print("Finished fetching Draft (Picks/Bans Table) Data.")
    return dict(team_drafts)
# Helper functions
def get_champion(span_tag):
    if span_tag and 'title' in span_tag.attrs:
        return span_tag['title']
    return "N/A"

def get_role_from_sprite(role_sprite):
    style = role_sprite['style']
    if "background-position:-32px -16px" in style:
        return "Top"
    elif "background-position:-32px -0px" in style:
        return "Jungle"
    elif "background-position:-48px -0px" in style:
        return "Mid"
    elif "background-position:-16px -0px" in style:
        return "ADC"
    elif "background-position:-16px -16px" in style:
        return "Support"
    return "Unknown"

def normalize_champion_name(champ):
    if champ == "N/A":
        return "N/A"
    champion_exceptions = {
        "Nunu & Willump": "Nunu",
        "Xin Zhao": "XinZhao",
        "Miss Fortune": "MissFortune",
        "Kai'Sa": "Kaisa",
        "Kha'Zix": "Khazix",
        "LeBlanc": "Leblanc",
        "Wukong": "MonkeyKing",
        "Cho'Gath": "Chogath",
        "Jarvan IV": "JarvanIV",
        "Ivern": "Ivern",
        "K'Sante": "KSante",
        "Renata Glasc": "Renata"
    }
    champ_clean = champ.strip().lower()
    for full_name, normalized_name in champion_exceptions.items():
        if champ_clean == full_name.lower() or champ_clean.replace(" ", "").replace("&", "").replace("'", "") == full_name.lower().replace(" ", "").replace("&", "").replace("'", ""):
            return normalized_name
    champ_normalized = champ.replace(" ", "").replace("'", "").replace(".", "").replace("&", "").replace("-", "")
    return ''.join(word.capitalize() for word in champ_normalized.split())

def get_champion_icon(champion):
    if champion == "N/A":
        return "N/A"
    normalized_champ = normalize_champion_name(champion)
    icon_url = f"https://ddragon.leagueoflegends.com/cdn/{PATCH_VERSION}/img/champion/{normalized_champ}.png"
    return f'<img src="{icon_url}" width="35" height="35" style="vertical-align: middle;">'

def color_win_rate(value):
    if 0 <= value < 50:
        return f'<span style="color:rgb(255, 251, 251)">{value:.2f}</span>'
    elif 50 <= value <= 53:
        return f'<span style="color:rgb(204, 204, 31)">{value:.2f}</span>'
    else:
        return f'<span style="color:rgb(245, 26, 11)">{value:.2f}</span>'

# NEW: SoloQ functions
def setup_google_sheets():
    # Определяем scope
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

    # Получаем данные сервисного аккаунта из переменной окружения
    json_creds = os.getenv("GOOGLE_SHEETS_CREDS")
    if not json_creds:
        st.error("Не удалось загрузить учетные данные Google Sheets.")
        return None

    # Парсим JSON-строку в словарь
    creds_dict = json.loads(json_creds)

    # Авторизуемся с использованием словаря
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

def check_if_worksheets_exists(spreadsheet, name):
    try:
        wks = spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        wks = spreadsheet.add_worksheet(title=name, rows=1200, cols=10)
    return wks

def rate_limit_pause(start_time, request_count):
    REQUEST_LIMIT = 100
    TIME_WINDOW = 120
    if request_count >= REQUEST_LIMIT:
        elapsed_time = time.time() - start_time
        if elapsed_time < TIME_WINDOW:
            time.sleep(TIME_WINDOW - elapsed_time)
        return 0, time.time()
    return request_count, start_time

def get_account_data(worksheet, game_name, tag_line):
    game_ids = set(worksheet.col_values(2))  # Матч_айди из второй колонки
    request_count = 0
    start_time = time.time()

    response = requests.get(SUMMONER_NAME_BY_URL.format(game_name, tag_line))
    request_count += 1
    request_count, start_time = rate_limit_pause(start_time, request_count)

    if response.status_code == 200:
        data = response.json()
        puu_id = data["puuid"]
        match_history_response = requests.get(MATCH_HISTORY_URL.format(puu_id))
        request_count += 1
        request_count, start_time = rate_limit_pause(start_time, request_count)

        if match_history_response.status_code == 200:
            matches = match_history_response.json()
            new_data = []

            for game_id in matches:
                if game_id not in game_ids:
                    match_info_response = requests.get(MATCH_BASIC_URL.format(game_id))
                    request_count += 1
                    request_count, start_time = rate_limit_pause(start_time, request_count)

                    if match_info_response.status_code == 200:
                        match_data = match_info_response.json()
                        participants = match_data['metadata']['participants']
                        player_index = participants.index(puu_id)
                        player_data = match_data['info']['participants'][player_index]
                        champion_name = player_data['championName']
                        kills = player_data['kills']
                        deaths = player_data['deaths']
                        assists = player_data['assists']
                        position = player_data['teamPosition']
                        is_win = 1 if player_data["win"] else 0
                        game_creation = datetime.fromtimestamp(match_data['info']['gameCreation'] / 1000)

                        new_data.append([
                            game_creation.strftime('%Y-%m-%d %H:%M:%S'),
                            game_id,
                            is_win,
                            champion_name,
                            position,
                            kills,
                            deaths,
                            assists
                        ])

            if new_data:
                worksheet.append_rows(new_data)
            return new_data
    return None

def aggregate_soloq_data(spreadsheet, team_name):
    data = defaultdict(lambda: defaultdict(lambda: {
        "count": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0
    }))
    players = team_rosters.get(team_name, {})

    for player, player_data in players.items():
        wks = check_if_worksheets_exists(spreadsheet, player)
        full_data = wks.get_all_values()
        if not full_data:
            wks.append_row(["Дата матча", "Матч_айди", "Победа", "Чемпион", "Роль", "Киллы", "Смерти", "Ассисты"])
            continue
        for game_data in full_data[1:]:
            if len(game_data) >= 8:
                _, _, win, champion, role, kills, deaths, assists = game_data
                if champion and role == player_data["role"]:
                    if win == "1": data[player][champion]["wins"] += 1
                    data[player][champion]["count"] += 1
                    data[player][champion]["kills"] += int(kills)
                    data[player][champion]["deaths"] += int(deaths)
                    data[player][champion]["assists"] += int(assists)

    for player in data:
        data[player] = dict(sorted(data[player].items(), key=lambda x: (x[1]["count"], x[1]["wins"]), reverse=True))

    return data

# Main Streamlit function with button navigation
def main():


    # Initialize session state for page navigation
    if 'current_page' not in st.session_state:
        st.session_state.current_page = "Prime League Stats"

    st.sidebar.title("Navigation")
    
    # Prime League Teams selection
    if 'match_history_data' not in st.session_state or 'first_bans_data' not in st.session_state or 'draft_data' not in st.session_state:
        with st.spinner("Loading data from Leaguepedia..."):
            st.session_state.match_history_data = fetch_match_history_data()
            
            st.session_state.draft_data = fetch_draft_data()

    all_teams = set()
    for team in st.session_state.match_history_data.keys():
        all_teams.add(normalize_team_name(team))
    for team in st.session_state.first_bans_data.keys():
        all_teams.add(normalize_team_name(team))
    for team in st.session_state.draft_data.keys():
        all_teams.add(normalize_team_name(team))
    
    teams = sorted(list(all_teams))
    if not teams:
        st.warning("No teams found in the data.")
        return

    selected_team = st.sidebar.selectbox("Select a Prime League Team", teams, key="prime_team_select")

    # Button to switch to UOL SoloQ
    if st.session_state.current_page == "Prime League Stats":
        if st.sidebar.button("Go to UOL SoloQ"):
            st.session_state.current_page = "UOL SoloQ"
            st.rerun()

    # Добавляем логотип и текст внизу бокового меню
    st.sidebar.markdown("<hr style='border: 1px solid #333; margin: 20px 0;'>", unsafe_allow_html=True)
    
    # Логотип Unicorns of Love
    st.sidebar.image("uol_logo.png", width=100, use_container_width=True)  # Заменили use_column_width на use_container_width
    
    # Текст "by heovech"
    st.sidebar.markdown(
        """
        <div style="text-align: center; font-size: 14px; color: #888;">
            by heovech
        </div>
        """,
        unsafe_allow_html=True
    )

    # Render the appropriate page
    if st.session_state.current_page == "Prime League Stats":
        prime_league_page(selected_team)
    elif st.session_state.current_page == "UOL SoloQ":
        soloq_page()

def save_notes_data(data, team_name, filename_prefix="notes_data"):
    """Сохраняет данные в JSON-файл, уникальный для каждой команды."""
    filename = f"{filename_prefix}_{team_name}.json"
    with open(filename, "w") as f:
        json.dump(data, f)

def load_notes_data(team_name, filename_prefix="notes_data"):
    """Загружает данные из JSON-файла для конкретной команды. Если файла нет, возвращает начальные данные."""
    filename = f"{filename_prefix}_{team_name}.json"
    default_data = {
        "tables": [
            [
                ["", "Ban", ""],
                ["", "Ban", ""],
                ["", "Ban", ""],
                ["", "Pick", ""],
                ["", "Pick", ""],
                ["", "Pick", ""],
                ["", "Ban", ""],
                ["", "Ban", ""],
                ["", "Pick", ""],
                ["", "Pick", ""]
            ] for _ in range(6)  # 6 таблиц с пустыми данными
        ],
        "notes_text": ""  # Пустое поле для заметок
    }
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return default_data

def save_notes_data(data, team_name, filename_prefix="notes_data"):
    """Сохраняет данные в JSON-файл, уникальный для каждой команды."""
    filename = f"{filename_prefix}_{team_name}.json"
    # Создаем директорию, если ее нет
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    try:
        with open(filename, "w", encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"Error saving notes data for {team_name}: {e}")

def load_notes_data(team_name, filename_prefix="notes_data"):
    """Загружает данные из JSON-файла для конкретной команды. Если файла нет, возвращает начальные данные."""
    filename = f"{filename_prefix}_{team_name}.json"
    default_data = {
        "tables": [
            [
                ["", "Ban", ""], ["", "Ban", ""], ["", "Ban", ""],
                ["", "Pick", ""], ["", "Pick", ""], ["", "Pick", ""],
                ["", "Ban", ""], ["", "Ban", ""],
                ["", "Pick", ""], ["", "Pick", ""]
            ] for _ in range(6)
        ],
        "notes_text": ""
    }
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding='utf-8') as f:
                # Проверяем, не пустой ли файл
                content = f.read()
                if content:
                    return json.loads(content)
                else:
                    return default_data
        except json.JSONDecodeError:
             st.warning(f"Could not decode JSON from {filename}. Returning default notes.")
             return default_data
        except Exception as e:
            st.error(f"Error loading notes data for {team_name}: {e}")
            return default_data
    return default_data
# --- Конец функций save/load notes ---


def prime_league_page(selected_team):
    st.title("Prime League 1st Division 2025 Spring - Pick & Ban Statistics")

    normalized_selected_team = normalize_team_name(selected_team)

    st.header(f"Team: {selected_team}")

    # --- Кнопка обновления и проверка данных в session_state ---
    # Убедимся, что все данные загружены при необходимости
    data_loaded = ('match_history_data' in st.session_state and
                   'first_bans_data' in st.session_state and
                   'draft_data' in st.session_state)

    col_update, col_status = st.columns([1, 5])
    with col_update:
        if st.button("Update Data"):
            with st.spinner("Updating data... This may take a while."):
                st.session_state.match_history_data = fetch_match_history_data()
                st.session_state.first_bans_data = fetch_first_bans_data()
                st.session_state.draft_data = fetch_draft_data()
            st.success("Data updated!")
            st.rerun() # Перезапускаем для отображения обновленных данных
    with col_status:
         if not data_loaded:
              st.warning("Data not fully loaded. Click 'Update Data'.")

    # Загрузка данных, если их нет
    if not data_loaded:
         if 'match_history_data' not in st.session_state:
              st.session_state.match_history_data = fetch_match_history_data()
         if 'first_bans_data' not in st.session_state:
              st.session_state.first_bans_data = fetch_first_bans_data()
         if 'draft_data' not in st.session_state:
              st.session_state.draft_data = fetch_draft_data()
         st.rerun() # Перезапускаем после загрузки

    # Initialize session state for button toggles if not exists
    if 'show_picks' not in st.session_state: st.session_state.show_picks = False
    if 'show_bans' not in st.session_state: st.session_state.show_bans = False
    if 'show_duo_picks' not in st.session_state: st.session_state.show_duo_picks = False
    if 'show_drafts' not in st.session_state: st.session_state.show_drafts = True # По умолчанию показываем драфты
    if 'show_notes' not in st.session_state: st.session_state.show_notes = False

    # Button controls for main sections
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        if st.button("Picks", key="picks_btn", use_container_width=True): st.session_state.show_picks = not st.session_state.show_picks
    with col2:
        if st.button("Bans", key="bans_btn", use_container_width=True): st.session_state.show_bans = not st.session_state.show_bans
    with col3:
        if st.button("Duo Picks", key="duo_picks_btn", use_container_width=True): st.session_state.show_duo_picks = not st.session_state.show_duo_picks
    with col4:
        if st.button("Drafts", key="drafts_btn", use_container_width=True): st.session_state.show_drafts = not st.session_state.show_drafts
    with col5:
        if st.button("Notes", key="notes_btn", use_container_width=True): st.session_state.show_notes = not st.session_state.show_notes

    # --- Отображение блоков Picks, Bans, Duo Picks (оставляем как было) ---
    team_info = st.session_state.match_history_data.get(normalized_selected_team, {})
    first_bans_info = st.session_state.first_bans_data.get(normalized_selected_team, {'BlueFirstBans': defaultdict(int), 'RedFirstBans': defaultdict(int)})
    roles = ['Top', 'Jungle', 'Mid', 'ADC', 'Support']

    if st.session_state.show_picks:
        st.subheader("Picks")
        st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)
        columns = st.columns(len(roles))
        for i, role in enumerate(roles):
            with columns[i]:
                st.subheader(f"{role}")
                # Используем данные из match_history для пиков по ролям
                role_data = team_info.get(role, {})
                if role_data:
                    stats = []
                    for champ, data in role_data.items():
                        if champ != "N/A" and data.get('games', 0) > 0:
                            winrate = (data['wins'] / data['games'] * 100) if data['games'] > 0 else 0
                            stats.append({
                                'Icon': get_champion_icon(champ),
                                'Champion': champ,
                                'Matches': data['games'],
                                'Win Rate (%)': winrate
                            })
                    if stats:
                        df = pd.DataFrame(stats)
                        df = df.sort_values('Matches', ascending=False)
                        df['Win Rate (%)'] = df['Win Rate (%)'].apply(color_win_rate)
                        # Убираем пустую колонку индекса
                        html = df.to_html(escape=False, index=False, classes='styled-table small-table')
                        st.markdown(html, unsafe_allow_html=True)
                    else:
                         st.write("No pick data for this role.")
                else:
                    st.write("No data structure for this role.")


    if st.session_state.show_bans:
        # Все строки ниже должны иметь ОДИНАКОВЫЙ, больший отступ (например, +4 пробела)
        st.subheader("Bans") # <--- Строка 888, начало блока с отступом
        st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)
        col1, col2, divider_col, col3, col4 = st.columns([1, 1, 0.1, 1, 1])

        # --- Первые 3 бана команды (Данные из st.session_state.draft_data) ---
        all_draft_data = st.session_state.get('draft_data', {})
        # Используйте .get(), чтобы избежать KeyError, если normalized_selected_team не найден
        draft_data_list = all_draft_data.get(normalized_selected_team, [])

        # Собираем статистику по первым 3 банам команды на каждой стороне
        team_blue_first3_bans = defaultdict(int)
        team_red_first3_bans = defaultdict(int)
        if draft_data_list:
            for draft in draft_data_list:
                side = draft.get('side')
                # team_bans должен быть списком из 5 банов ['B1', 'B2', 'B3', 'B4', 'B5']
                # Используем .get() для безопасного доступа
                team_bans = draft.get('team_bans', [])
                if side == 'blue':
                    for ban in team_bans[:3]: # Берем первые три
                        if ban != "N/A": team_blue_first3_bans[ban] += 1
                elif side == 'red':
                    for ban in team_bans[:3]: # Берем первые три
                        if ban != "N/A": team_red_first3_bans[ban] += 1

        with col1:
            st.subheader("First 3 Bans (as Blue Side)")
            if team_blue_first3_bans:
                blue_bans_stats = []
                # Сортируем по убыванию количества
                sorted_blue_bans = sorted(team_blue_first3_bans.items(), key=lambda item: item[1], reverse=True)
                for champ, count in sorted_blue_bans:
                    # Добавим проверку на N/A перед использованием get_champion_icon
                    if champ != "N/A":
                        blue_bans_stats.append({
                            'Icon': get_champion_icon(champ), 'Champion': champ, 'Count': count
                        })
                if blue_bans_stats: # Проверяем, есть ли что отображать
                    df_blue_bans = pd.DataFrame(blue_bans_stats)
                    html_blue_bans = df_blue_bans.to_html(escape=False, index=False, classes='styled-table small-table')
                    st.markdown(html_blue_bans, unsafe_allow_html=True)
                else:
                    st.write("No valid first 3 blue side bans data.") # Уточнено сообщение
            else:
                st.write("No data for first 3 blue side bans.")

        with col2:
            st.subheader("First 3 Bans (as Red Side)")
            if team_red_first3_bans:
                red_bans_stats = []
                 # Сортируем по убыванию количества
                sorted_red_bans = sorted(team_red_first3_bans.items(), key=lambda item: item[1], reverse=True)
                for champ, count in sorted_red_bans:
                     if champ != "N/A":
                        red_bans_stats.append({
                            'Icon': get_champion_icon(champ), 'Champion': champ, 'Count': count
                        })
                if red_bans_stats: # Проверяем, есть ли что отображать
                    df_red_bans = pd.DataFrame(red_bans_stats)
                    html_red_bans = df_red_bans.to_html(escape=False, index=False, classes='styled-table small-table')
                    st.markdown(html_red_bans, unsafe_allow_html=True)
                else:
                    st.write("No valid first 3 red side bans data.") # Уточнено сообщение
            else:
                st.write("No data for first 3 red side bans.")

        # --- Первые 3 бана оппонента (Данные из st.session_state.match_history_data) ---
        # Используем .get() для безопасного доступа
        team_info = st.session_state.match_history_data.get(normalized_selected_team, {})

        with divider_col:
            st.markdown(
                """<div style='height: 100%; border-left: 2px solid #333; margin: 0 10px;'></div>""",
                unsafe_allow_html=True
            )

        with col3:
            st.subheader("Opponent's First 3 Bans (vs Blue)") # Когда ваша команда была BLUE
            # Берем баны оппонента (КРАСНОГО), когда вы были синими
            # Используем .get() для безопасного доступа
            opponent_red_bans_data = team_info.get('OpponentRedBansFirst3', {})
            if opponent_red_bans_data:
                opponent_red_bans_stats = []
                sorted_opp_red = sorted(opponent_red_bans_data.items(), key=lambda item: item[1], reverse=True)
                for champ, count in sorted_opp_red:
                     if champ != "N/A":
                        opponent_red_bans_stats.append({
                            'Icon': get_champion_icon(champ), 'Champion': champ, 'Count': count
                        })
                if opponent_red_bans_stats: # Проверяем, есть ли что отображать
                    df_opponent_red_bans = pd.DataFrame(opponent_red_bans_stats)
                    html_opponent_red_bans = df_opponent_red_bans.to_html(escape=False, index=False, classes='styled-table small-table')
                    st.markdown(html_opponent_red_bans, unsafe_allow_html=True)
                else:
                    st.write("No valid opponent first 3 red bans data.") # Уточнено сообщение
            else:
                st.write("No data structure for opponent's first 3 red bans.")

        with col4:
            st.subheader("Opponent's First 3 Bans (vs Red)") # Когда ваша команда была RED
            # Берем баны оппонента (СИНЕГО), когда вы были красными
            # Используем .get() для безопасного доступа
            opponent_blue_bans_data = team_info.get('OpponentBlueBansFirst3', {})
            if opponent_blue_bans_data:
                opponent_blue_bans_stats = []
                sorted_opp_blue = sorted(opponent_blue_bans_data.items(), key=lambda item: item[1], reverse=True)
                for champ, count in sorted_opp_blue:
                     if champ != "N/A":
                        opponent_blue_bans_stats.append({
                            'Icon': get_champion_icon(champ), 'Champion': champ, 'Count': count
                        })
                if opponent_blue_bans_stats: # Проверяем, есть ли что отображать
                    df_opponent_blue_bans = pd.DataFrame(opponent_blue_bans_stats)
                    html_opponent_blue_bans = df_opponent_blue_bans.to_html(escape=False, index=False, classes='styled-table small-table')
                    st.markdown(html_opponent_blue_bans, unsafe_allow_html=True)
                else:
                     st.write("No valid opponent first 3 blue bans data.") # Уточнено сообщение
            else:
                st.write("No data structure for opponent's first 3 blue bans.")

    if st.session_state.show_duo_picks:
         # --- Блок Duo Picks (оставляем как было) ---
        st.subheader("Duo Picks")
        st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)
        duo_picks_data = team_info.get('DuoPicks', {})
        duo_pairs_config = [
            {'roles': ('Top', 'Jungle'), 'title': 'Top-Jungle'},
            {'roles': ('Jungle', 'Mid'), 'title': 'Jungle-Mid'},
            {'roles': ('Jungle', 'Support'), 'title': 'Jungle-Support'},
            {'roles': ('ADC', 'Support'), 'title': 'ADC-Support'}
        ]

        num_cols = 2 # Количество колонок для дуо-пиков
        cols = st.columns(num_cols)
        col_idx = 0

        for config in duo_pairs_config:
            with cols[col_idx % num_cols]:
                role1_target, role2_target = config['roles']
                title = config['title']
                st.markdown(f"<h4 style='text-align: center;'>{title} Duo Picks</h4>", unsafe_allow_html=True)

                duo_stats = []
                for (champ1, champ2, r1, r2), data in duo_picks_data.items():
                    # Проверяем обе комбинации ролей
                    if (r1 == role1_target and r2 == role2_target) or \
                       (r1 == role2_target and r2 == role1_target):
                        if data.get('games', 0) > 0 and champ1 != "N/A" and champ2 != "N/A":
                            winrate = (data['wins'] / data['games'] * 100) if data['games'] > 0 else 0

                            # Определяем порядок для отображения
                            c1_display, c2_display = (champ1, champ2) if r1 == role1_target else (champ2, champ1)
                            icon1 = get_champion_icon(c1_display)
                            icon2 = get_champion_icon(c2_display)

                            duo_stats.append({
                                f'Icon_{role1_target}': icon1,
                                role1_target: c1_display,
                                f'Icon_{role2_target}': icon2,
                                role2_target: c2_display,
                                'Matches': data['games'],
                                'Win Rate (%)': winrate
                            })

                if duo_stats:
                    df_duo = pd.DataFrame(duo_stats)
                    df_duo = df_duo.sort_values('Matches', ascending=False)
                    df_duo['Win Rate (%)'] = df_duo['Win Rate (%)'].apply(color_win_rate)
                    # Формируем правильные колонки
                    display_columns = [f'Icon_{role1_target}', role1_target, f'Icon_{role2_target}', role2_target, 'Matches', 'Win Rate (%)']
                    df_duo = df_duo[display_columns]
                    html_duo = df_duo.to_html(escape=False, index=False, classes='styled-table small-table')
                    st.markdown(f"""<div style="display: flex; justify-content: center;">{html_duo}</div>""", unsafe_allow_html=True)
                else:
                    st.markdown(f"""<p style='text-align: center;'>No data on duo picks for {title}.</p>""", unsafe_allow_html=True)
            col_idx += 1 # Переходим к следующей колонке

    # --- ИЗМЕНЕННЫЙ БЛОК DRAFTS ---
    if st.session_state.show_drafts:
        st.subheader("Drafts")
        st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)

        # Используем УЖЕ загруженные данные для текущей команды
        all_draft_data = st.session_state.get('draft_data', {})
        draft_data_list = all_draft_data.get(normalized_selected_team, [])

        if draft_data_list:
            # Group drafts by match_key (team pair) and sort by match number within the group
            drafts_by_match = defaultdict(list)
            for draft in draft_data_list:
                 # Добавим проверку на наличие match_key, на всякий случай
                 match_key = draft.get('match_key')
                 if match_key:
                    drafts_by_match[match_key].append(draft)

            # Sort matches based on the minimum match_number in each group
            # Это приближение к порядку недель/игр
            sorted_match_keys = sorted(drafts_by_match.keys(),
                                       key=lambda k: min(d.get('match_number', float('inf')) for d in drafts_by_match[k]))

            # Display each match series
            for match_key in sorted_match_keys:
                match_drafts = sorted(drafts_by_match[match_key], key=lambda d: d.get('match_number', 0)) # Сортируем игры внутри серии

                if not match_drafts: continue # Пропускаем, если нет драфтов для этого ключа

                # Определяем команды из первого драфта серии
                first_draft = match_drafts[0]
                # Используем absolute_blue_team/absolute_red_team для заголовка
                abs_blue = first_draft.get('absolute_blue_team', 'Blue Team')
                abs_red = first_draft.get('absolute_red_team', 'Red Team')
                opponent_name = first_draft.get('opponent', 'Opponent')

                st.subheader(f"{normalized_selected_team} vs {opponent_name}") # Заголовок серии

                # Initialize session state for toggling games visibility within this match series
                base_game_key = f"show_game_{'_'.join(map(str, match_key))}" # Уникальный ключ для серии
                for draft in match_drafts:
                    match_num = draft.get('match_number', 0)
                    game_toggle_key = f"{base_game_key}_{match_num}"
                    if game_toggle_key not in st.session_state:
                        st.session_state[game_toggle_key] = False # По умолчанию скрыты

                # Create buttons to toggle each game's visibility
                num_games = len(match_drafts)
                game_cols = st.columns(min(num_games, 5)) # Показывать максимум 5 кнопок в ряд
                for i, draft in enumerate(match_drafts):
                    with game_cols[i % 5]:
                        match_num = draft.get('match_number', i + 1)
                        game_toggle_key = f"{base_game_key}_{match_num}"
                        # Показываем счет серии на кнопке
                        blue_score = draft.get('blue_wins_series', 0)
                        red_score = draft.get('red_wins_series', 0)
                        button_label = f"Game {match_num} ({abs_blue} {blue_score} - {red_score} {abs_red})"

                        if st.button(button_label, key=f"game_btn_{game_toggle_key}", use_container_width=True):
                            st.session_state[game_toggle_key] = not st.session_state[game_toggle_key]

                # Display games that are toggled on for this series
                active_games_in_series = [draft for draft in match_drafts
                                          if st.session_state.get(f"{base_game_key}_{draft.get('match_number', 0)}")]

                if active_games_in_series:
                    active_cols = st.columns(len(active_games_in_series)) # Колонки для отображения драфтов
                    for i, draft in enumerate(active_games_in_series):
                        with active_cols[i]:
                            match_num = draft.get('match_number', 'N/A')
                            side = draft.get('side')
                            winner_side = draft.get('winner_side')

                            is_winner = False
                            if side and winner_side:
                                is_winner = (side == winner_side)
                            result = "Win" if is_winner else "Loss"

                            st.write(f"**Game {match_num}**")
                            st.write(f"**Result: {result}** ({side.capitalize()} Side)")

                            # Определяем заголовки колонок (Выбранная команда | Оппонент)
                            left_team_header = normalized_selected_team
                            right_team_header = draft.get('opponent', 'Opponent')

                            # Получаем данные из драфта (используем .get для безопасности)
                            team_bans = draft.get('team_bans', ['N/A']*5)
                            opponent_bans = draft.get('opponent_bans', ['N/A']*5)
                            team_picks = draft.get('team_picks_ordered', ['N/A']*5)         # Используем ordered
                            opponent_picks = draft.get('opponent_picks_ordered', ['N/A']*5) # Используем ordered

                            vod_link = draft.get('vod_link', "N/A")
                            vod_html = f'<a href="{vod_link}" target="_blank">VOD</a>' if vod_link != "N/A" else ""

                            # --- Формируем данные для таблицы драфта (10 строк) ---
                            table_data = []
                            # Баны Фаза 1 (3 строки)
                            for ban_idx in range(3):
                                tb = team_bans[ban_idx] if ban_idx < len(team_bans) else "N/A"
                                ob = opponent_bans[ban_idx] if ban_idx < len(opponent_bans) else "N/A"
                                info = vod_html if ban_idx == 0 else result if ban_idx == 2 else "" # VOD в 1й, Result в 3й
                                table_data.append((
                                    f"{get_champion_icon(tb)} {tb}" if tb != "N/A" else "",
                                    "Ban",
                                    f"{get_champion_icon(ob)} {ob}" if ob != "N/A" else "",
                                    info
                                ))
                            # Пики Фаза 1 (3 строки - P1, P2, P3)
                            for pick_idx in range(3):
                                tp = team_picks[pick_idx] if pick_idx < len(team_picks) else "N/A"
                                op = opponent_picks[pick_idx] if pick_idx < len(opponent_picks) else "N/A"
                                table_data.append((
                                    f"{get_champion_icon(tp)} {tp}" if tp != "N/A" else "",
                                    "Pick",
                                    f"{get_champion_icon(op)} {op}" if op != "N/A" else "",
                                    ""
                                ))
                            # Баны Фаза 2 (2 строки - B4, B5)
                            for ban_idx in range(3, 5):
                                tb = team_bans[ban_idx] if ban_idx < len(team_bans) else "N/A"
                                ob = opponent_bans[ban_idx] if ban_idx < len(opponent_bans) else "N/A"
                                table_data.append((
                                    f"{get_champion_icon(tb)} {tb}" if tb != "N/A" else "",
                                    "Ban",
                                    f"{get_champion_icon(ob)} {ob}" if ob != "N/A" else "",
                                    ""
                                ))
                            # Пики Фаза 2 (2 строки - P4, P5)
                            for pick_idx in range(3, 5):
                                tp = team_picks[pick_idx] if pick_idx < len(team_picks) else "N/A"
                                op = opponent_picks[pick_idx] if pick_idx < len(opponent_picks) else "N/A"
                                table_data.append((
                                    f"{get_champion_icon(tp)} {tp}" if tp != "N/A" else "",
                                    "Pick",
                                    f"{get_champion_icon(op)} {op}" if op != "N/A" else "",
                                    ""
                                ))

                            # Создаем DataFrame
                            df_draft = pd.DataFrame(table_data, columns=[left_team_header, "Action", right_team_header, "Info"])

                            # Функция стилизации (можно оставить или адаптировать цвета)
                            def highlight_draft_cells(row):
                                styles = [''] * len(row)
                                action = row['Action']
                                info = row['Info']
                                left_content = row[left_team_header]
                                right_content = row[right_team_header]

                                base_style = 'text-align: center; vertical-align: middle;'
                                ban_color = '#4d0f0f' # Темно-красный
                                pick_color = '#002b4d' # Темно-синий
                                win_color = 'green'
                                loss_color = 'red'
                                text_color = 'white'

                                # Стиль для Action
                                styles[1] = f'{base_style} font-weight: bold;'

                                if action == "Ban":
                                    if left_content: styles[0] = f'{base_style} background-color: {ban_color}; color: {text_color};'
                                    else: styles[0] = base_style
                                    styles[1] += f' color: {ban_color};' # Цвет текста Ban
                                    if right_content: styles[2] = f'{base_style} background-color: {ban_color}; color: {text_color};'
                                    else: styles[2] = base_style
                                elif action == "Pick":
                                    if left_content: styles[0] = f'{base_style} background-color: {pick_color}; color: {text_color};'
                                    else: styles[0] = base_style
                                    styles[1] += f' color: {pick_color};' # Цвет текста Pick
                                    if right_content: styles[2] = f'{base_style} background-color: {pick_color}; color: {text_color};'
                                    else: styles[2] = base_style
                                else: # Пустые строки или другое
                                     styles[0] = base_style
                                     styles[2] = base_style

                                # Стиль для Info (VOD/Result)
                                styles[3] = base_style # Базовый стиль для ячейки Info
                                if "VOD" in info: # Если есть ссылка VOD
                                    pass # Оставляем стандартный стиль для ссылки
                                elif info == "Win":
                                    styles[3] += f' background-color: {win_color}; color: {text_color}; font-weight: bold;'
                                elif info == "Loss":
                                    styles[3] += f' background-color: {loss_color}; color: {text_color}; font-weight: bold;'

                                return styles

                            # Применяем стили и отображаем HTML
                            styled_df = df_draft.style.apply(highlight_draft_cells, axis=1)
                            html_draft = styled_df.to_html(escape=False, index=False, classes='styled-table drafts-table small-table')
                            st.markdown(html_draft, unsafe_allow_html=True)
                else:
                    # Если нет активных игр для отображения в этой серии
                    # Можно добавить st.write("Select a game button above to view the draft.")
                    pass # Или ничего не показывать

        else:
            st.write(f"No draft data found for {normalized_selected_team}.")


    # --- Блок Notes (оставляем как есть, но добавляем обработку ошибок при загрузке/сохранении) ---
    if st.session_state.show_notes:
        st.subheader("Notes")
        st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)

        notes_data_key = f'notes_data_{normalized_selected_team}'
        if notes_data_key not in st.session_state:
            st.session_state[notes_data_key] = load_notes_data(normalized_selected_team, "data/notes") # Сохраняем в папку data

        col_left, col_right = st.columns([3, 1])

        with col_left:
            st.subheader("Draft Templates")
            table_cols = st.columns(3)
            current_notes_data = st.session_state[notes_data_key]
            # Убедимся, что tables существует и это список
            if "tables" not in current_notes_data or not isinstance(current_notes_data["tables"], list) or len(current_notes_data["tables"]) != 6:
                 st.warning("Notes table data is corrupted. Resetting to default.")
                 current_notes_data["tables"] = default_notes_data["tables"] # default_notes_data нужно определить

            for i in range(6):
                with table_cols[i % 3]:
                    st.write(f"Draft Template {i + 1}")
                    columns = ["Team 1", "Action", "Team 2"]
                     # Проверка данных таблицы перед созданием DataFrame
                    table_content = current_notes_data["tables"][i]
                    if not isinstance(table_content, list) or not all(isinstance(row, list) and len(row) == 3 for row in table_content):
                         st.warning(f"Invalid data for table {i+1}. Resetting.")
                         table_content = default_notes_data["tables"][0] # Берем структуру из дефолта
                         current_notes_data["tables"][i] = table_content


                    try:
                        df = pd.DataFrame(table_content, columns=columns)
                        edited_df = st.data_editor(
                            df,
                            num_rows="fixed", # Фикс. кол-во строк (10)
                            use_container_width=True,
                            key=f"notes_table_{normalized_selected_team}_{i}",
                            height=385, # Примерная высота для 10 строк
                            column_config={
                                "Team 1": st.column_config.TextColumn("Team 1", width="medium"),
                                "Action": st.column_config.SelectboxColumn("Action", width="small", options=["Ban", "Pick"], required=True),
                                "Team 2": st.column_config.TextColumn("Team 2", width="medium"),
                            }
                        )
                        # Обновляем данные в сессии
                        st.session_state[notes_data_key]["tables"][i] = edited_df.values.tolist()
                    except Exception as e:
                         st.error(f"Error displaying notes table {i+1}: {e}")
                         # Отображаем пустой редактор как запасной вариант
                         df_empty = pd.DataFrame([["", "Ban", ""]] * 10, columns=columns)
                         st.data_editor(df_empty, key=f"notes_table_{normalized_selected_team}_{i}_fallback", disabled=True)


        with col_right:
            st.subheader("Additional Notes")
            # Убедимся, что notes_text существует
            if "notes_text" not in current_notes_data or not isinstance(current_notes_data["notes_text"], str):
                 current_notes_data["notes_text"] = ""

            notes_text = st.text_area(
                "Write your notes here:",
                value=current_notes_data["notes_text"],
                height=800, # Увеличим высоту
                key=f"notes_text_area_{normalized_selected_team}"
            )
            st.session_state[notes_data_key]["notes_text"] = notes_text

        # Кнопка сохранения может быть полезна, если автосохранение нежелательно
        # if st.button("Save Notes"):
        save_notes_data(st.session_state[notes_data_key], normalized_selected_team, "data/notes")
            # st.success("Notes saved!")
def soloq_page():
    st.title("Unicorns of Love Sexy Edition 2025 SoloQ Statistics")

    # Кнопка для возврата на страницу Prime League Stats
    if st.button("Back to Prime League Stats"):
        st.session_state.current_page = "Prime League Stats"
        st.rerun()

    # Подключение к Google Sheets
    client = setup_google_sheets()
    if not client:
        return

    try:
        spreadsheet = client.open("Soloq_UOL")
    except gspread.exceptions.APIError as e:
        st.error(f"Ошибка подключения к Google Sheets: {str(e)}")
        return

    # Инициализация данных в session_state
    if 'soloq_data' not in st.session_state:
        st.session_state.soloq_data = aggregate_soloq_data(spreadsheet, "Unicorns of Love Sexy Edition")

    # Кнопка обновления данных
    if st.button("Update Soloq"):
        with st.spinner("Updating SoloQ data..."):
            for player, player_data in team_rosters["Unicorns of Love Sexy Edition"].items():
                wks = check_if_worksheets_exists(spreadsheet, player)
                for game_name, tag_line in zip(player_data["game_name"], player_data["tag_line"]):
                    get_account_data(wks, game_name, tag_line)
            st.session_state.soloq_data = aggregate_soloq_data(spreadsheet, "Unicorns of Love Sexy Edition")
        st.success("SoloQ data updated!")

    # Секция статистики игроков
    st.subheader("SoloQ Player Statistics")
    st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)
    soloq_data = st.session_state.soloq_data
    players = team_rosters["Unicorns of Love Sexy Edition"].keys()
    cols = st.columns(5)
    for i, player in enumerate(players):
        with cols[i]:
            st.subheader(f"{player} Stats")
            wks = check_if_worksheets_exists(spreadsheet, player)
            data = wks.get_all_values()
            if len(data) > 1:
                df = pd.DataFrame(data[1:], columns=["Дата матча", "Матч_айди", "Победа", "Чемпион", "Роль", "Киллы", "Смерти", "Ассисты"])
                df["Дата матча"] = pd.to_datetime(df["Дата матча"], errors='coerce')
                time_filter = st.selectbox(f"Filter {player}", ["All", "1 week", "2 weeks", "4 weeks"], key=f"time_filter_{player}")
                if time_filter != "All":
                    days = {"1 week": 7, "2 weeks": 14, "4 weeks": 28}[time_filter]
                    cutoff = datetime.now() - timedelta(days=days)
                    df = df[df["Дата матча"] >= cutoff]
                player_data = defaultdict(lambda: {"count": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0})
                for _, row in df.iterrows():
                    if row["Роль"] == team_rosters["Unicorns of Love Sexy Edition"][player]["role"]:
                        champion = row["Чемпион"]
                        if row["Победа"] == "1": player_data[champion]["wins"] += 1
                        player_data[champion]["count"] += 1
                        player_data[champion]["kills"] += int(row["Киллы"])
                        player_data[champion]["deaths"] += int(row["Смерти"])
                        player_data[champion]["assists"] += int(row["Ассисты"])
                stats = []
                for champ, stats_dict in player_data.items():
                    if stats_dict["count"] > 0:
                        win_rate = round(stats_dict["wins"] / stats_dict["count"] * 100, 2)
                        kda = round((stats_dict["kills"] + stats_dict["assists"]) / max(stats_dict["deaths"], 1), 2)
                        stats.append({"Champion": champ, "Games": stats_dict["count"], "Win Rate (%)": win_rate, "KDA": kda})
                if stats:
                    df_stats = pd.DataFrame(stats).sort_values("Games", ascending=False)
                    df_stats["Win Rate (%)"] = df_stats["Win Rate (%)"].apply(color_win_rate)
                    html = df_stats.to_html(escape=False, index=False, classes='styled-table')
                    st.markdown(html, unsafe_allow_html=True)
                else:
                    st.write(f"No SoloQ data for {player}.")
            else:
                st.write(f"No SoloQ data for {player}.")

    # Секция визуализации
    st.subheader("SoloQ Games Over Time")
    st.markdown("<hr style='border: 2px solid #333; margin: 10px 0;'>", unsafe_allow_html=True)

    # Выбор игрока и периода
    selected_player = st.selectbox("Select Player for Visualization", players, key="viz_player")
    aggregation_type = st.selectbox("Aggregate by", ["Day", "Week", "Month"], key="agg_type")

    # Получение данных для выбранного игрока
    wks = check_if_worksheets_exists(spreadsheet, selected_player)
    try:
        data = wks.get_all_values()
        if len(data) <= 1:
            st.write("No data available for visualization.")
            return

        # Преобразование данных в DataFrame
        df = pd.DataFrame(data[1:], columns=["Дата матча", "Матч_айди", "Победа", "Чемпион", "Роль", "Киллы", "Смерти", "Ассисты"])
        df["Дата матча"] = pd.to_datetime(df["Дата матча"], errors='coerce')
        df = df.dropna(subset=["Дата матча"])  # Удаляем строки без даты

        # Агрегация данных
        if aggregation_type == "Day":
            df_agg = df.groupby(df["Дата матча"].dt.date).size().reset_index(name="Games")
            df_agg.columns = ["Дата", "Количество игр"]
            title = f"Games Played per Day by {selected_player}"
            st.bar_chart(df_agg.set_index("Дата")["Количество игр"])
        
        elif aggregation_type == "Week":
            df_agg = df.groupby(df["Дата матча"].dt.to_period("W")).size().reset_index(name="Games")
            df_agg["Дата матча"] = df_agg["Дата матча"].apply(lambda x: x.start_time)  # Начало недели
            df_agg.columns = ["Дата", "Количество игр"]
            title = f"Games Played per Week by {selected_player}"
            st.bar_chart(df_agg.set_index("Дата")["Количество игр"])
        
        elif aggregation_type == "Month":
            df_agg = df.groupby(df["Дата матча"].dt.to_period("M")).size().reset_index(name="Games")
            df_agg["Дата матча"] = df_agg["Дата матча"].apply(lambda x: x.start_time)  # Начало месяца
            df_agg.columns = ["Дата", "Количество игр"]
            title = f"Games Played per Month by {selected_player}"
            st.bar_chart(df_agg.set_index("Дата")["Количество игр"])

        # Вывод заголовка
        if not df_agg.empty:
            st.write(f"**{title}**")
        else:
            st.write(f"No data available for visualization for {selected_player}.")

    except gspread.exceptions.APIError as e:
        st.error(f"Ошибка API Google Sheets при загрузке данных: {str(e)}")


# Аутентификация (вставляем здесь)
with open('config.yaml') as file:
    config = yaml.load(file, Loader=SafeLoader)

# Инициализация Authenticate
authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

# Проверяем, есть ли уже результат авторизации в сессии
if 'authentication_status' not in st.session_state:
    st.session_state.authentication_status = None
    st.session_state.name = None
    st.session_state.username = None

# Если пользователь ещё не авторизован, показываем форму логина
if st.session_state.authentication_status is None:
    login_result = authenticator.login(key='Login')
    if login_result is not None:
        st.session_state.name, st.session_state.authentication_status, st.session_state.username = login_result

# Извлекаем значения из st.session_state (они гарантированно существуют)
name = st.session_state.name
authentication_status = st.session_state.authentication_status
username = st.session_state.username

# Логика обработки авторизации
if authentication_status:
    # Пользователь авторизован
    with st.sidebar:
        authenticator.logout('Logout', 'sidebar')
        st.write(f'Welcome *Coach*')
    
    if __name__ == "__main__":
        main()

elif authentication_status == False:
    st.error('Username/password is incorrect')
elif authentication_status is None:
    st.warning('Please enter your username and password')
