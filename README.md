# 🕸️ Liquipedia Scraper API

A lightweight and efficient Python + Flask API designed to scrape tournament, team, and event data for esports games (e.g., Dota 2, CS:GO, LoL) from [Liquipedia](https://liquipedia.net). This API provides structured access to esports data, with built-in caching and database storage for improved performance.

---

## 📦 Features

- ✅ **Tournament Data**: Fetch tournaments categorized as:
  - **Ongoing**: Currently active tournaments.
  - **Upcoming**: Future tournaments.
  - **Completed**: Past tournaments.
- ✅ **Team and Event Data**: Retrieve Esports World Cup (EWC) 2025 teams and events, with caching in a SQLite database for faster subsequent requests.
- ✅ **Caching System**: 
  - Tournament data is cached in the `cache/` directory for 10 minutes to reduce API calls to Liquipedia.
  - Team and event data are stored persistently in a SQLite database, with an option to fetch live data.
- ✅ **Force Refresh**: Use the `force` parameter to bypass cache and fetch fresh tournament data, or the `live` parameter for teams and events.
- ✅ **Database Integration**: Stores news, teams, and events in a SQLite database for efficient data management.
- ✅ **Image Uploads**: Supports uploading images for news articles with validation for common image formats (PNG, JPG, JPEG, GIF, WebP).
- ✅ **Pagination and Filtering**: News endpoint supports pagination, writer filtering, and search functionality.
- ✅ **Swagger Documentation**: Interactive API documentation via Swagger for easy testing and exploration.
- ✅ **CORS Support**: Allows cross-origin requests for flexible frontend integration.
- ✅ **Simple and Extensible**: Clean codebase, easy to modify and extend for additional features.

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.8+**
- **pip** for installing dependencies
- **Git** for cloning the repository
- A working internet connection to fetch data from Liquipedia

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/mahmoodhamdi/scraper_api.git
   cd scraper_api
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application locally:
   ```bash
   python app.py
   ```

The API will be available at `http://127.0.0.1:5000`.

---

## 🧪 API Endpoints

### 1. Fetch Tournaments
```http
POST /api/tournaments
```
**Request Body (JSON)**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| game  | string  | ✅        | Game slug (e.g., `dota2`, `csgo`, `lol`) |
| force | boolean | ❌        | Bypass cache and fetch fresh data (default: `false`) |

**Example Request**:
```json
{
  "game": "dota2",
  "force": true
}
```

**Example Response**:
```json
{
  "Ongoing": [
    {
      "name": "The International 2025",
      "link": "https://liquipedia.net/dota2/The_International/2025",
      "dates": "July 10-20, 2025"
    }
  ],
  "Upcoming": [...],
  "Completed": [...]
}
```

### 2. Fetch Matches
```http
POST /api/matches
```
**Request Body (JSON)**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| game  | string  | ✅        | Game slug (e.g., `dota2`, `csgo`, `lol`) |
| force | boolean | ❌        | Bypass cache and fetch fresh data (default: `false`) |

**Example Response**:
```json
{
  "Ongoing": [...],
  "Upcoming": [...],
  "Completed": [...]
}
```

### 3. Fetch EWC Matches
```http
POST /api/ewc_matches
```
**Request Body (JSON)**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| game  | string  | ✅        | Game slug (e.g., `dota2`, `csgo`, `lol`) |

**Example Response**:
```json
{
  "message": "Match data retrieved successfully for dota2",
  "data": {
    "Group A": [
      {
        "Team1": { "Name": "Team A", "Logo": "https://liquipedia.net/..." },
        "Team2": { "Name": "Team B", "Logo": "https://liquipedia.net/..." },
        "MatchTime": "July 8, 2025 - 10:00 CEST",
        "Score": "0-0"
      }
    ]
  }
}
```

### 4. Fetch EWC Matches by Day
```http
POST /api/ewc_matches_by_day
```
**Request Body (JSON)**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| game  | string  | ✅        | Game slug (e.g., `dota2`, `csgo`, `lol`) |
| date  | string  | ❌        | Filter by date (YYYY-MM-DD)              |

**Example Response**:
```json
{
  "message": "Match data retrieved successfully for dota2",
  "data": {
    "2025-07-08": {
      "Group A": [...],
      "Group B": [...]
    }
  }
}
```

### 5. Fetch EWC Matches by Date
```http
POST /api/ewc_matches_by_date
```
**Request Body (JSON)**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| game  | string  | ✅        | Game slug (e.g., `dota2`, `csgo`, `lol`) |
| date  | string  | ✅        | Specific date (YYYY-MM-DD)               |

**Example Response**:
```json
{
  "message": "Match data retrieved successfully for dota2 on 2025-07-08",
  "data": {
    "Group A": [...],
    "Group B": [...]
  }
}
```

### 6. Fetch EWC Teams
```http
GET /api/ewc_teams
```
**Query Parameters**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| live  | boolean | ❌        | Fetch live data from Liquipedia (default: `false`) |

**Example Response**:
```json
{
  "message": "Teams data retrieved successfully",
  "data": [
    {
      "team_name": "Team Liquid",
      "logo_url": "https://liquipedia.net/..."
    }
  ]
}
```

### 7. Fetch EWC Events
```http
GET /api/ewc_events
```
**Query Parameters**:
| Key   | Type    | Required | Description                              |
|-------|---------|----------|------------------------------------------|
| live  | boolean | ❌        | Fetch live data from Liquipedia (default: `false`) |

**Example Response**:
```json
{
  "message": "Events data retrieved successfully",
  "data": [
    {
      "name": "Dota 2",
      "link": "https://liquipedia.net/dota2/..."
    }
  ]
}
```

### 8. News Management
- **Create News**: `POST /api/news` (multipart/form-data with title, writer, description, thumbnail_url/file, news_link)
- **Get News**: `GET /api/news` (supports pagination, writer filter, and search)
- **Update News**: `PUT /api/news/<id>` (multipart/form-data)
- **Delete News**: `DELETE /api/news/<id>`
- **Delete All News**: `DELETE /api/news`

### 9. Reset Database
```http
POST /api/reset_db
```
**Request Body (JSON)**:
| Key           | Type    | Required | Description                              |
|---------------|---------|----------|------------------------------------------|
| clear_uploads | boolean | ❌        | Delete all uploaded files (default: `false`) |

**Example Response**:
```json
{
  "message": "Database reset successfully"
}
```

---

## 🗃️ Data Storage and Caching

- **Tournament Cache**:
  - Stored in the `cache/` directory as JSON files.
  - Cache expires after **10 minutes**.
  - Use `force: true` to bypass cache and fetch fresh data.
- **Team and Event Storage**:
  - Persisted in a SQLite database (`news.db`) for efficient retrieval.
  - Data is fetched from Liquipedia on the first request or when `live=true` is specified.
  - Subsequent requests retrieve data from the database unless `live=true`.
- **News Storage**:
  - Stored in the `news` table of the SQLite database.
  - Supports thumbnails via URL or file upload, stored in `static/uploads/`.
- **Database Schema**:
  - `news`: Stores news articles (id, title, description, writer, thumbnail_url, news_link, created_at, updated_at).
  - `teams`: Stores EWC team data (id, team_name, logo_url, updated_at).
  - `events`: Stores EWC event data (id, name, link, updated_at).

---

## 🚀 Deployment

### Deploying on PythonAnywhere

1. **Setup**: Ensure your PythonAnywhere account is configured with SSH access.
2. **Deploy Script**: Use the provided `deploy.sh` script to automate deployment:
   ```bash
   bash deploy.sh
   ```
   The script:
   - Pushes changes to GitHub.
   - Connects to PythonAnywhere via SSH.
   - Pulls the latest code.
   - Installs dependencies.
   - Restarts the web app.

3. **Manual Deployment**:
   - Clone the repository on PythonAnywhere:
     ```bash
     git clone https://github.com/mahmoodhamdi/scraper_api.git
     ```
   - Install dependencies:
     ```bash
     pip install --user -r requirements.txt
     ```
   - Configure the web app in PythonAnywhere's dashboard (set working directory and WSGI file).

### Environment Variables
- None required by default, but you can add configuration for:
  - `UPLOAD_FOLDER`: Custom path for uploaded images.
  - `DATABASE_PATH`: Custom path for `news.db`.

---

## 📂 Project Structure

```structure
scraper_api/
├── app.py                    # Main Flask application
├── scraper/
│   └── liquipedia_scraper.py # Scraping logic for tournaments and matches
├── static/
│   └── uploads/             # Directory for uploaded news images
├── cache/                   # Directory for cached tournament data
├── news.db                  # SQLite database for news, teams, and events
├── deploy.sh                # Deployment script for PythonAnywhere
├── requirements.txt         # Python dependencies
└── README.md                # Project documentation
```

---

## 🔍 Testing the API

Use tools like **Postman**, **cURL**, or **Swagger UI** (accessible at `/apidocs`) to test the API.

**Example cURL for Tournaments**:
```bash
curl -X POST http://127.0.0.1:5000/api/tournaments \
-H "Content-Type: application/json" \
-d '{"game": "dota2", "force": true}'
```

**Swagger UI**:
- Access at `http://127.0.0.1:5000/apidocs` for interactive API testing.

---

## 🛠️ Troubleshooting

- **No data returned**: Ensure the `game` slug is correct (e.g., `dota2`, not `Dota 2`). Check Liquipedia's availability.
- **Database errors**: Verify `news.db` has write permissions and the `static/uploads/` directory exists.
- **Image upload fails**: Confirm the file type is supported (PNG, JPG, JPEG, GIF, WebP).
- **Cache issues**: Set `force: true` for tournaments or `live: true` for teams/events to fetch fresh data.
- **Deployment issues**: Check PythonAnywhere logs for errors and ensure dependencies are installed.

---

## ✨ Future Improvements

- 🆕 **More Games**: Expand support for additional esports titles (e.g., Valorant, Overwatch).
- 🆕 **Detailed Data**: Include more tournament details (e.g., prize pools, teams, schedules).
- 🆕 **Advanced Caching**: Implement a configurable cache duration and Redis for distributed caching.
- 🆕 **Authentication**: Add API key or OAuth for secure access.
- 🆕 **Rate Limiting**: Prevent abuse with request rate limits.
- 🆕 **Webhooks**: Notify clients of new tournaments or match updates.
- 🆕 **Frontend Integration**: Build a simple frontend to visualize the scraped data.

---

## 🤝 Contributing

Contributions are welcome! To contribute:

1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/YourFeature`).
3. Commit your changes (`git commit -m 'Add YourFeature'`).
4. Push to the branch (`git push origin feature/YourFeature`).
5. Open a Pull Request.

Please include tests and update the README if necessary.

---

## 📜 License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

## 👨‍💻 About the Developer

- 👤 **Mahmoud Hamdy**
- 📧 Email: [hmdy7486@gmail.com](mailto:hmdy7486@gmail.com)
- 🌐 GitHub: [mahmoodhamdi](https://github.com/mahmoodhamdi)
- 💻 Powered by: Python, Flask, BeautifulSoup, SQLite, Flasgger

---

## 🙏 Acknowledgments

- **Liquipedia**: For providing comprehensive esports data.
- **Flask**: For the lightweight web framework.
- **BeautifulSoup**: For robust HTML parsing.
- **PythonAnywhere**: For easy hosting and deployment.

Feel free to star ⭐ this repository if you find it useful!
