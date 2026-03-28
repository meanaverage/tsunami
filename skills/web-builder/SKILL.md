# Web Application Builder

Build complete, deployable web applications from natural language descriptions.

## Instructions

When activated for a web development task:

1. **Scaffold phase**: Create the project directory structure under workspace/deliverables/
2. **Backend phase**: Write the server (Python Flask/FastAPI preferred, Node.js if requested)
3. **Frontend phase**: Write HTML/CSS/JS (vanilla preferred unless framework requested)
4. **Integration phase**: Connect frontend to backend, add any necessary API routes
5. **Test phase**: Run the application locally, verify it starts, test basic functionality
6. **Deliver phase**: Provide the user with run instructions and the project path

## Architecture Preferences

- Python + Flask for simple apps (fewest dependencies)
- Python + FastAPI for API-heavy apps (async, auto-docs)
- Vanilla HTML/CSS/JS for frontends (no build step needed)
- SQLite for persistence (zero-config database)
- File-based storage for simple key-value needs

## Quality Standards

- The app MUST start with a single command (e.g., `python app.py`)
- Include a README.md with setup and run instructions
- Handle basic errors (404, 500, invalid input)
- Use semantic HTML and clean CSS
- No external CDN dependencies — bundle or inline everything

## Do NOT

- Use React/Vue/Angular unless explicitly requested
- Require Docker for simple apps
- Add authentication unless requested
- Over-engineer — match complexity to the request
