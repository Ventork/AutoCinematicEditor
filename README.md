
# Auto Cinematic Editor Cloud

Cloud-ready Flask + FFmpeg version for phone/browser use.

Features:
- 7 BAB workflow
- Paste narration script directly into textarea
- Upload audio + multiple images per BAB
- 16:9 1920x1080, 30 FPS
- 2–8 second target scene timing when physically possible
- Audio-energy pacing
- Multiple Ken Burns motions
- Background render with progress polling
- Final MP4 download
- Docker image includes FFmpeg
- `/health` endpoint

Deploy:
1. Put this project in a GitHub repository.
2. Deploy the repository as a Docker Web Service on Render or Railway.
3. Use the generated public URL from the hosting dashboard.

Important:
This is a cloud deployment foundation. The Director modules from the local V2 workspace can be merged later; this package intentionally stays self-contained so deployment is reliable.
