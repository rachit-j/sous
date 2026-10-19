# Project Proposal

**Option A: Build a human-centered AI application**

Brainstorming

List of problem spaces/pain points: 

1. Morphed interface for non-ai native users  
2. Save for later problem \-\> map it into categories/make it easy to go back to?   
3. Organize screenshots  
4. Expense splitting (smart split based on menu/input of what people ordered and a pic of the receipt, smart follow-up nudges to those who haven’t paid. These are additional features on top of existing apps like splitwise/venmo).   
5. 

– – – – – – Choose group: – – – – –  
Students  
– – – – – Problems w/ students – – – – –

Option 2: Use your phone as a standby assistant for cooking. Can help monitor you as you are cooking and can help plan and track recipes. Watch cooking live as it happens, keeping alerts.

**Problem and target user:** What real-world problem are you addressing, and who is the target user?

- People often spend a lot of time cooking, but a lot of that time is spent watching the food actually cook instead of physically cooking. Sous aims to reclaim some of that time, acting as a second chef to monitor your cooking especially during long cooks so you can take control of your time in the kitchen.   
- Many of us are amateur chefs in the kitchen, and although we have a recipe and instructions in hand, the cooking in practice may not always go as planned. Sous aims to provide guidance that includes live monitoring and feedback, plan and track recipes and optimize your cooking time. The target users are people who are in-experienced, cooking under time constraints, and could also extend to restaurants looking to add additional monitoring/compliance guidance. 

**Why bother:** why does this matter, and where do current tools fall short?

- Existing kitchen tools offer basic recipes, standard timers, or purely reactive AI interactions.  
- Current VLM solutions lack persistent state tracking across ongoing steps, failing to monitor active progress or determine optimal moments for user intervention.




**Your idea:** What kind of application do you imagine building? And describe the interaction you have in mind. Vague is fine at this stage.

- Users mount their phone above their cooking workspace before they start cooking, and they explain their cooking plan to Sous. Sous can then be set in two modes: monitoring, where it acts safely in the background, and assistive, where it guides you live on the next steps.

**Reach goal:** If everything goes well, what's the best possible version of this?

- The reach version of Sous would be a system that supports relatively free-form cooking rather than requiring a structured recipe plan. A person should be able to use voice-to-text or just input a plan of what they would be cooking, and Sous should be able to create a recipe while tracking its state as the user cooks.  
- The agent should be able to store past context so it can decide how you cook. If the user leans towards less agent interaction, then the AI should spend more time in monitoring mode. If the user relies more on the agent, the agent should be able to speak (using speakers) and alert the user better.

**Minimum goal:** If things go badly, what's the minimum you'd still be able to turn in?

- If things go badly, we would be able to support a small set of recipes with clearly defined steps and visual transitions.  
- We would be able to track the current recipe step, timers, and periodically analyze camera frames.  
- We would be able to alert the user about a few supported states and allow them to even correct the state of the agent.

**Platform, interface and interaction:**  What form will your system take, and how will a user interact with it? For example a web app, mobile app, local app, browser extension, command line tool, notebook widget, or conversational interface. Whatever suits your users.

- We will start with a mobile-friendly web application that uses both the camera and microphone.  
- It will have a minimal interface showing the current step, timers, and what Sous currently believes is happening (Sous’s state).  
- The interaction we are aiming for is a verbal audio-based interaction as often cooking is a hands-on job, so the agent has to be hands free.

**Connection to class:** Where does the human fit, and which class concepts will you apply? (e.g., error types and their effect on user experience, explainability, trust calibration, privacy, or fairness.)

- Mixed Initiative: deciding when/how the agent should intervene versus staying silent.  
- Trust/error handling: how do people *trust* Sous enough to let it watch over cooking? In the case of fires? (There may be sensor-based potential for deterministic fallbacks, however, we will not have time to dig into this for different platforms this semester)  
- Human Agency/Interaction: keeping the cook in control while minimizing unnecessary interruptions so that the human is not distracted.

**Learning goals:** What do you each hope to learn from doing this? Ideally tied to specific parts of the project.

- Learn how to build a real-time multimodal system around ongoing activities.  
- Focus on augmenting the human through variable persistent state modeling instead of prompt-based interaction.  
- Study how users respond to AI errors, uncertainty, and different levels of proactive intervention.

**Models, APIs, or data you expect to use**

- Remote VLM such as ChatGPT, Claude, or on-device VLM such as Qwen3 VL ([8B](https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct) probably for now).   
- Speech-to-text and text-to-speech (Whisper/Kokoro?) for voice commands and alerts  
- Web camera APIs plus a backend/database for recipes, timers, observations, and task state.

