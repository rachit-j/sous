# Provider setup

Use **Settings** for Bedrock credentials, region and model selection, with passphrase-encrypted credential storage. Environment configuration remains supported for existing provider setups. All saved secrets stay on the server after submission; the settings API never returns them. Sous never downloads models or silently switches providers. See [secure settings](settings.md).

## Ollama

Run your existing Ollama service with an installed image-capable model. Set `OLLAMA_BASE_URL` (default `http://localhost:11434`) and `OLLAMA_MODEL` to the model's installed identifier. A text-only model will not work for webcam monitoring. No download or installation is performed by Sous.

The adapter uses image-bearing messages with `/api/chat` and disables streaming. [Official chat API](https://docs.ollama.com/api/chat).

The first request may load a cold model into memory. Sous allows a longer local inference timeout and keeps manual steps/timers usable while analysis runs. The current development machine already has `qwen3-vl:8b`; other machines must install an image-capable model separately or use a hosted provider.

## OpenAI

Set `OPENAI_API_KEY` and `OPENAI_MODEL` to an image-capable API model available to your account. A ChatGPT subscription/login does not configure this application API key. The adapter uses the Responses API with image inputs. [Official vision guide](https://developers.openai.com/api/docs/guides/images-vision).

## Anthropic

Set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` to an available Claude model accepting image input. Claude Code authentication is separate from this application's API configuration. The adapter uses the Messages API. [Official vision guide](https://platform.claude.com/docs/en/build-with-claude/vision).

## AWS Bedrock

Open **Settings**, create an unlock passphrase, and save your AWS access key ID, secret access key, optional session token and region. Load model candidates, select an image-capable model or enter a model/inference-profile ID, and save it as your default. No `.env` changes are required. Saved credential inputs remain blank; blank fields retain saved values, with explicit controls to remove a session token or connection. Unlock again after the server restarts. A forgotten passphrase cannot be recovered.

Discovery uses AWS metadata APIs and does not invoke a model or send camera images. Listing permission may be absent even if invocation works, so a custom ID remains supported. Listings do not guarantee access or Converse compatibility. Your credentials must permit invoking the selected model/profile in the chosen region. For least privilege, grant only the model invocation and discovery operations you need.

Legacy setups can still use `AWS_REGION`, `BEDROCK_MODEL_ID` and the AWS credential chain, optionally `AWS_PROFILE`. Managed saved credentials take precedence; a locked managed connection must be unlocked rather than silently switching identity. Never paste keys into project documentation, commands, or issue reports.

The adapter uses Boto3's Bedrock Runtime Converse API. Image/model support varies; verify your selected model/region in AWS. [Converse API](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html), [model compatibility](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html).

## Troubleshooting

- A configured entry is not proof the model/credentials/network work; it means server settings are present.
- Use Analyze now after framing the pan/workspace. Failed requests leave the current step unchanged.
- Check the selected provider/model and server configuration before retrying. Errors shown by Sous omit upstream response bodies to avoid leaking credentials.
- Capture is periodic rather than continuous video. Local models may take longer than the sample interval; requests never intentionally overlap.
- Settings controls the interval from1–15 seconds (default10). Shorter intervals can increase cloud usage; slow requests still finish before another analysis begins.
