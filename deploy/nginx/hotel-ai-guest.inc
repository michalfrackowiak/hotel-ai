# Include only in the HTTPS server for the approved Google JavaScript origin.
# The reception snippet supplies the proxy headers and timeouts.
# Keep this allowlist and size limit aligned with guest_accounts.

# Override Nginx's automatic slash redirects for these credential routes.
location = /guest/session { return 404; }
location = /guest/google/nonce { return 404; }
location = /guest/google/sign-in { return 404; }
location = /guest/sign-out { return 404; }

location = /guest/session/ {
    limit_req zone=hotel_ai_guest burst=5 nodelay;
    limit_req_status 429;
    client_max_body_size 4224;
    proxy_pass http://127.0.0.1:8080/guest/session/;
}

location = /guest/google/nonce/ {
    limit_req zone=hotel_ai_guest burst=5 nodelay;
    limit_req_status 429;
    client_max_body_size 4224;
    proxy_pass http://127.0.0.1:8080/guest/google/nonce/;
}

location = /guest/google/sign-in/ {
    limit_req zone=hotel_ai_guest burst=5 nodelay;
    limit_req_status 429;
    client_max_body_size 4224;
    proxy_pass http://127.0.0.1:8080/guest/google/sign-in/;
}

location = /guest/sign-out/ {
    limit_req zone=hotel_ai_guest burst=5 nodelay;
    limit_req_status 429;
    client_max_body_size 4224;
    proxy_pass http://127.0.0.1:8080/guest/sign-out/;
}
