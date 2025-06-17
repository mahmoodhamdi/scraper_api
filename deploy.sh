#!/bin/bash

# إعداداتك
GIT_REPO_DIR="/f/scraper_api"
PA_USERNAME="ashwah"
PA_HOST="$PA_USERNAME@ssh.pythonanywhere.com"
PA_PROJECT_DIR="/home/$PA_USERNAME/scraper_api"

echo "🚀 Step 1: Commit & push to GitHub"
cd "$GIT_REPO_DIR" || exit
git add .
git commit -m "🚀 Auto deploy"
git push

echo "🖥️ Step 2: Pulling from GitHub on PythonAnywhere..."
ssh "$PA_HOST" <<EOF
cd "$PA_PROJECT_DIR"
git pull
EOF

echo "🌐 Step 3: Reloading the web app..."
ssh "$PA_HOST" <<EOF
touch /var/www/ashwah_pythonanywhere_com_wsgi.py
EOF

echo "✅ Deployment complete!"
