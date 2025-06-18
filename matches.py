import requests
from bs4 import BeautifulSoup
import json
import os

def get_matches_by_status(game="worldoftanks"):
    url = f"https://liquipedia.net/{game}/Liquipedia:Matches"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }

    response = requests.get(url, headers=headers)
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


    with open("matches_upcoming.json", "w", encoding="utf-8") as f:
        json.dump(all_data["upcoming"], f, ensure_ascii=False, indent=2)

    with open("matches_completed.json", "w", encoding="utf-8") as f:
        json.dump(all_data["completed"], f, ensure_ascii=False, indent=2)


    print("\n Upcoming Matches:")
    print(json.dumps(all_data["upcoming"], ensure_ascii=False, indent=2))

    print("\n Completed Matches:")
    print(json.dumps(all_data["completed"], ensure_ascii=False, indent=2))

    return all_data


get_matches_by_status("worldoftanks")
