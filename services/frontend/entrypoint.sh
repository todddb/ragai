#!/bin/sh
set -e

# Set FRONTEND_ASSET_VERSION if not provided (use timestamp)
if [ -z "$FRONTEND_ASSET_VERSION" ]; then
    FRONTEND_ASSET_VERSION=$(date +%s)
    echo "FRONTEND_ASSET_VERSION not set, using timestamp: $FRONTEND_ASSET_VERSION"
else
    echo "Using FRONTEND_ASSET_VERSION: $FRONTEND_ASSET_VERSION"
fi

# Replace __ASSET_VERSION__ placeholder in all HTML files
echo "Replacing __ASSET_VERSION__ in HTML files..."
find /usr/share/nginx/html -type f -name "*.html" -exec sed -i "s/__ASSET_VERSION__/$FRONTEND_ASSET_VERSION/g" {} \;

echo "Asset version substitution complete. Starting nginx..."

# Start nginx in foreground
exec nginx -g 'daemon off;'
