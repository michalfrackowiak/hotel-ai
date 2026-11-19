## Include only from the HTTPS server block.
location = /admin {
    return 308 /admin/;
}

location = /admin/login/ {
    limit_req zone=hotel_ai_admin_login burst=5 nodelay;
    proxy_pass http://127.0.0.1:8080/admin/login/;
}

location ^~ /admin/ {
    proxy_pass http://127.0.0.1:8080/admin/;
}

location ^~ /static/admin/ {
    proxy_pass http://127.0.0.1:8080/static/admin/;
}
