# Provider setup

Copy `.env.example` to `.env` and configure only the providers you want. Restart Flask after configuration changes. All credentials and endpoint URLs remain on the server; the browser chooses a provider and model ID. Sous never downloads models or silently switches providers.

## Ollama

Run your existing Ollama service with an installed image-capable model. Set `OLLAMA_BASE_URL` (default `http://localhost:11434`) and `OLLAMA_MODEL` to the model's installed identifier. A text-only model will not work for webcam monitoring. No download or installation is performed by Sous.

The adapter uses image-bearing messages with `/api/chat` and disables streaming. [Official chat API](https://docs.ollama.com/api/chat).

## OpenAI

Set `OPENAI_API_KEY` and `OPENAI_MODEL` to an image-capable API model available to your account. A ChatGPT subscription/login does not configure this application API key. The adapter uses the Responses API with image inputs. [Official vision guide](https://developers.openai.com/api/docs/guides/images-vision).

## Anthropic

Set `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` to an available Claude model accepting image input. Claude Code authentication is separate from this application's API configuration. The adapter uses the Messages API. [Official vision guide](https://platform.claude.com/docs/en/build-with-claude/vision).

## AWS Bedrock

Set `AWS_REGION` and `BEDROCK_MODEL_ID` to an image-capable model or inference-profile ID with Converse support. Use the AWS standard credential chain, optionally with `AWS_PROFILE`. Your credentials must permit invoking that model in the chosen region. Do not put AWS credentials in browser inputs or project documentation.

The adapter uses Boto3's Bedrock Runtime Converse API. Image/model support varies; verify your selected model/region in AWS. [Converse API](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html), [model compatibility](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html).

## Troubleshooting

- A configured entry is not proof the model/credentials/network work; it means server settings are present.
- Use Analyze now after framing the pan/workspace. Failed requests leave the current step unchanged.
- Check the selected provider/model and server configuration before retrying. Errors shown by Sous omit upstream response bodies to avoid leaking credentials.
- Capture is periodic rather than continuous video. Local models may take longer than the sample interval; requests never intentionally overlap.
