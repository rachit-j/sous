# Provider setup

V3 uses one model provider: **AWS Bedrock**, through a single shared connection that an administrator saves in **Settings**. Every account on the instance uses it, so people trying out a demo need no AWS setup of their own. Local models (Ollama) and the unused OpenAI/Anthropic adapters were removed in V3. Saved secrets stay server-side; the API never returns them, and Sous never falls back to server environment credentials. See [secure settings](settings.md) and [V3 features](v3.md#shared-aws-connection).

## AWS Bedrock

An administrator opens **Settings**, creates the shared vault passphrase, and saves an AWS access key ID, secret access key, optional session token and region. They then load model candidates, select an image-capable model or enter a model/inference-profile ID, and save it as the shared model. No `.env` changes are required. Saved credential inputs remain blank; blank fields retain saved values, with explicit controls to remove a session token or connection.

After every server restart or deploy, an administrator unlocks the vault once and it serves everyone until the next restart or an explicit lock. Signing out does not lock it. A forgotten passphrase cannot be recovered.

Discovery uses AWS metadata APIs and does not invoke a model or send camera images. Listing permission may be absent even if invocation works, so a custom ID remains supported. Listings do not guarantee access or Converse compatibility. The credentials must permit invoking the selected model/profile, and Nova 2 Sonic for voice, in the chosen region. For least privilege, grant only the model invocation and discovery operations you need.

Sous ignores ambient AWS keys, `AWS_PROFILE` and legacy cloud defaults, including when no shared connection is saved or it is deleted. Never paste keys into documentation, commands or issue reports.

The adapter uses Boto3's Bedrock Runtime Converse API. Image/model support varies; verify your selected model/region in AWS. [Converse API](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html), [model compatibility](https://docs.aws.amazon.com/bedrock/latest/userguide/models-api-compatibility.html).

## Cost

Every account's camera checks, recipe drafts and voice calls are billed to the one shared AWS account. The snapshot interval (1–15 seconds, default 10) is per user; shorter intervals increase cost. Recipe generation is rate-limited per user, and only one voice stream runs instance-wide.

## Troubleshooting

- A configured entry is not proof the model/credentials/network work; it means the shared connection is saved and unlocked.
- "Locked" after a deploy means an administrator needs to unlock Settings. Manual cooking controls keep working meanwhile.
- Use Analyze now after framing the pan/workspace. Failed requests leave the current step unchanged.
- Errors shown by Sous omit upstream response bodies to avoid leaking credentials.
- Capture is periodic rather than continuous video; requests never intentionally overlap.
