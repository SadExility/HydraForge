⛏️ HydraForge

A simple Minecraft 1.8.9 mod builder made for Android.

I made HydraForge because compiling Minecraft mods directly from a phone isn't exactly the easiest thing to do.

The idea is pretty simple: select your project, send it to GitHub Actions, let it compile and download the JAR when it's ready.

No need to set up a whole development environment on your phone.

Features

- Compile Minecraft Forge 1.8.9 projects.
- Upload projects using ZIP files.
- Build remotely with GitHub Actions.
- Live build status and timer.
- Compilation logs.
- Automatic JAR download.
- Custom output directory.
- Minecraft-inspired interface.

What you need

- An Android device.
- A GitHub account.
- A Personal Access Token (Classic).
- A Minecraft Forge 1.8.9 project.

GitHub Token

HydraForge needs a GitHub token to upload files and start workflows.

1. Go to "GitHub Token Settings" (https://github.com/settings/tokens).
2. Click Generate new token (classic).
3. Enable these permissions:
   - "repo"
   - "workflow"
4. Generate your token and copy it.
5. Paste it into HydraForge.

Don't share your token with anyone.

How to use

1. Open HydraForge.
2. Paste your GitHub token.
3. Choose a repository name.
4. Select your project ZIP.
5. Choose where the generated JAR should be saved.
6. Hit the build button.
7. Wait for the compilation to finish.
8. Download your JAR.

That's pretty much it.

Build environment

Component| Version
Minecraft| 1.8.9
Java| 8
Gradle| 2.14.1
Ubuntu| 22.04

Disclaimer

This is an independent project and has no affiliation with Mojang, Microsoft, Minecraft Forge or GitHub.

Use it responsibly.

---

Made by SadExility.

AI slop BTW
