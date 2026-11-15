client_max_body_size 16k;
limit_req_status 429;
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_connect_timeout 3s;
proxy_read_timeout 35s;

location = / {
    limit_req zone=hotel_ai_chat burst=15 nodelay;
    proxy_pass http://127.0.0.1:8080/;
}

location = /healthz/ {
    proxy_pass http://127.0.0.1:8080/healthz/;
}

location = /my-booking/ {
    limit_req zone=hotel_ai_chat burst=15 nodelay;
    proxy_pass http://127.0.0.1:8080/my-booking/;
}

location = /availability/ {
    limit_req zone=hotel_ai_chat burst=15 nodelay;
    proxy_pass http://127.0.0.1:8080/availability/;
}

location = /reserve/ {
    limit_req zone=hotel_ai_chat burst=15 nodelay;
    proxy_pass http://127.0.0.1:8080/reserve/;
}

location = /reception/message/ {
    limit_req zone=hotel_ai_chat burst=15 nodelay;
    proxy_pass http://127.0.0.1:8080/reception/message/;
}

location = /reception/audio/ {
    limit_req zone=hotel_ai_audio burst=5 nodelay;
    proxy_pass http://127.0.0.1:8080/reception/audio/;
}

location = /reception/transcribe/ {
    client_max_body_size 480000;
    limit_req zone=hotel_ai_audio burst=5 nodelay;
    proxy_pass http://127.0.0.1:8080/reception/transcribe/;
}

location ^~ /static/reception/ {
    proxy_pass http://127.0.0.1:8080/static/reception/;
}

location / {
    return 404;
}
