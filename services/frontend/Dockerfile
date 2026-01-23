FROM nginx:alpine

# Copy entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Copy frontend files
COPY . /usr/share/nginx/html

# Use entrypoint script
ENTRYPOINT ["/entrypoint.sh"]
