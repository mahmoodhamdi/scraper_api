import requests
from bs4 import BeautifulSoup

def fetch_tournaments(game_slug):
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
