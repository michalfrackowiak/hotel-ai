# Reception model providers

Open Django admin and select **Reception agent configuration**. Add the configuration on first use, then edit it to switch providers. The default built-in demo accepts the dates and guest counts shown in the chat prompts. It requires no API key.

The configured model helps interpret free-form dates and guest counts the built-in parser cannot read. The app sends the current message from those steps to the configured endpoint. Guest name, email, price calculation, and booking confirmation use the Django conversation flow. Check the quoted dates and guest count before confirming a stay.

## Hosted OpenAI-compatible API

1. Set `HOTEL_MODEL_API_KEY` in the ignored `.env` file and run `docker compose up -d --force-recreate web` to load it.
2. Select **Hosted OpenAI-compatible API**. Enter the HTTPS base URL, including the API version when required, and the model ID. For OpenAI use `https://api.openai.com/v1` and a model available to your project that supports JSON mode in Chat Completions.
3. Open a new booking chat and try a date written in words. If the provider is unavailable, use the date format in the prompt to continue. [Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)

## Self-hosted Ollama

1. Start [Ollama](https://ollama.com/) on a machine reachable from Django and download a small multilingual model, such as `ollama pull qwen3:1.7b`.
2. Set `HOTEL_OLLAMA_URL` in `.env` to the Ollama server's base URL. The default `http://host.docker.internal:11434` points to the host from Docker Desktop. Recreate the web container after changing the URL.
3. Select **Self-hosted Ollama** in Django admin and enter `qwen3:1.7b` or the tag of an installed model. Model changes in admin apply to the next message. [Ollama Chat API](https://docs.ollama.com/api/chat) · [Qwen3 model](https://ollama.com/library/qwen3:1.7b)
