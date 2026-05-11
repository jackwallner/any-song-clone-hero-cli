#!/usr/bin/env bash
# Deploy SongHero landing page
# One command: cd landing && ./deploy.sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🎸 SongHero Deploy"
echo ""

deploy_surge() {
  if ! command -v surge &>/dev/null; then npm install -g surge; fi
  echo "→ Deploying with Surge..."
  surge . songhero.surge.sh
  echo ""
  echo "✅ Live at https://songhero.surge.sh"
  echo "   To use custom domain: surge . songhero.jackwallner.com"
  echo "   Add CNAME record: songhero.jackwallner.com → na-west1.surge.sh"
}

deploy_netlify() {
  if ! command -v netlify &>/dev/null; then npm install -g netlify-cli; fi
  echo "→ Deploying with Netlify..."
  netlify deploy --prod --dir=.
  echo ""
  echo "✅ Deployed!"
  echo "   Add custom domain in Netlify dashboard: songhero.jackwallner.com"
}

deploy_vercel() {
  echo "→ Deploying with Vercel..."
  npx vercel --prod --yes
  echo ""
  echo "✅ Deployed!"
}

echo "Choose deployment target:"
echo "  1) Surge (simplest, no config needed)"
echo "  2) Netlify (recommended, custom domain support)"
echo "  3) Vercel"
echo ""
read -p "Pick [1-3]: " choice

case $choice in
  1) deploy_surge ;;
  2) deploy_netlify ;;
  3) deploy_vercel ;;
  *) echo "Invalid choice"; exit 1 ;;
esac
