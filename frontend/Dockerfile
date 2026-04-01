FROM nginx:stable-alpine

# Copy built static assets
COPY frontend-dist /usr/share/nginx/html

# Replace default nginx config for routing /api to backend service
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
