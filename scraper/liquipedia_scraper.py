import os
import json
import time
import requests
from bs4 import BeautifulSoup

CACHE_DIR = "cache"
CACHE_DURATION = 10 * 60  # 10 دقائق

def fetch_tournaments(game_slug, force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{game_slug}_tournaments.json")

    if not force and os.path.exists(cache_path):
        last_modified = os.path.getmtime(cache_path)
        if time.time() - last_modified < CACHE_DURATION:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

    data = scrape_from_liquipedia(game_slug)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data

def scrape_from_liquipedia(game_slug):
    url = f"https://liquipedia.net/{game_slug}/Main_Page"
    headers = {'User-Agent': 'Mozilla/5.0'}

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {"error": f"Failed to fetch page. Status: {response.status_code}"}

    soup = BeautifulSoup(response.content, 'html.parser')
    sections = ['Upcoming', 'Ongoing', 'Completed']
    all_data = {}

    for section_name in sections:
        section_data = []
        section = soup.find("span", class_="tournaments-list-heading", string=section_name)

        if not section:
            all_data[section_name] = []
            continue

        ul = section.find_parent().find("ul", class_="tournaments-list-type-list")
        if not ul:
            all_data[section_name] = []
            continue

        items = ul.find_all("li")
        for item in items:
            name_tag = item.find("span", class_="tournament-name")
            link_tag = name_tag.find("a") if name_tag else None
            name = name_tag.text.strip() if name_tag else "No name"
            link = f"https://liquipedia.net{link_tag['href']}" if link_tag and link_tag.has_attr('href') else None

            date_tag = item.find("small", class_="tournaments-list-dates")
            date = date_tag.text.strip() if date_tag else "No date"

            tier_tag = item.find("div", class_="tournament-badge__chip")
            tier_text = tier_tag.text.strip() if tier_tag else "Unknown"
            tier_qualifier = item.find("div", class_="tournament-badge__text")
            tier = f"{tier_text} {tier_qualifier.text.strip()}" if tier_qualifier else tier_text

            logo_tag = item.find("span", class_="tournament-icon")
            logo_img = logo_tag.find("img") if logo_tag else None
            logo = f"https://liquipedia.net{logo_img['src']}" if logo_img and logo_img.has_attr("src") else None

            game_icon_tag = item.find("span", class_="tournament-game-icon")
            game_img = game_icon_tag.find("img") if game_icon_tag else None
            game_icon = f"https://liquipedia.net{game_img['src']}" if game_img and game_img.has_attr("src") else None

            section_data.append({
                "name": name,
                "date": date,
                "link": link,
                "tier": tier,
                "logo": logo,
                "game_icon": game_icon
            })

        all_data[section_name] = section_data

    return all_data

def get_matches_by_status(game="worldoftanks", force=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{game}_matches.json")

    if not force and os.path.exists(cache_path):
        last_modified = os.path.getmtime(cache_path)
        if time.time() - last_modified < CACHE_DURATION:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)

    url = f"https://liquipedia.net/{game}/Liquipedia:Matches"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }

    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return {"error": f"Failed to fetch matches. Status: {response.status_code}"}

    soup = BeautifulSoup(response.text, "html.parser")
    all_data = {"upcoming": {}, "completed": {}}
    sections = soup.select('div[data-toggle-area-content]')

    for section in sections:
        section_type = section.get('data-toggle-area-content')
        status_key = "upcoming" if section_type == "1" else "completed" if section_type == "2" else None
        if not status_key:
            continue

        matches = section.select('.match')

        for match in matches:
            team1 = match.select_one(".team-left .team-template-text a")
            team2 = match.select_one(".team-right .team-template-text a")
            rating1 = match.select_one(".team-left .team-rating")
            rating2 = match.select_one(".team-right .team-rating")

            result_text = ""
            score_parts = match.select(".versus-upper span")
            if score_parts and all(span.text.strip() for span in score_parts):
                result_text = ":".join([span.text.strip() for span in score_parts])
            else:
                rank_tag = match.select_one(".team-left .team-rank")
                if rank_tag:
                    result_text = rank_tag.text.strip()

            match_format = match.select_one(".versus-lower abbr")
            tournament = match.select_one(".match-tournament .tournament-name a")
            tournament_name = tournament.text.strip() if tournament else "Unknown Tournament"

            time_span = match.select_one("div.match-details > div.match-bottom-bar > span > span")
            alt_time = match.select_one(".timer-object-date")
            match_time = time_span.text.strip() if time_span else (alt_time.text.strip() if alt_time else "N/A")

            match_data = {
                "team1": team1.text.strip() if team1 else "N/A",
                "team2": team2.text.strip() if team2 else "N/A",
                "rating1": rating1.text.strip() if rating1 else "-",
                "rating2": rating2.text.strip() if rating2 else "-",
                "format": match_format.text.strip() if match_format else "-",
                "time": match_time
            }

            if result_text:
                match_data["result"] = result_text

            all_data[status_key].setdefault(tournament_name, []).append(match_data)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    return all_data
