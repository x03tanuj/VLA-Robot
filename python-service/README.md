# Python Service - Vision API

A Flask API that analyzes images and commands using OpenAI's GPT-4o vision model.

## Setup

1. Create a `.env` file based on `.env.example`:

   ```bash
   cp .env.example .env
   ```

2. Add your OpenAI API key to `.env`:

   ```
   OPENAI_API_KEY=sk-...your-key-here
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Server

```bash
python app.py
```

The server will start on `http://localhost:5000`

## API Endpoints

### POST /analyze

Analyzes an image with a text command using GPT-4o vision.

**Request:**

```json
{
  "image": "base64_encoded_image_string",
  "command": "pick up the red cup"
}
```

**Response:**

```json
{
  "object": "red cup",
  "action": "pick",
  "x": 45.5,
  "y": 60.2,
  "confidence": 0.95
}
```

**Fields:**

- `object` (string): The identified object from the command
- `action` (string): One of "pick", "move", or "place"
- `x` (float): X-coordinate as percentage of image width (0-100)
- `y` (float): Y-coordinate as percentage of image height (0-100)
- `confidence` (float): Confidence in the analysis (0-1)

### GET /health

Health check endpoint. Returns `{"status": "ok"}`.

## Error Handling

The API returns descriptive error messages in case of failures:

- Missing required fields
- Invalid image or command
- OpenAI API errors
- Invalid response format

All errors include an `error` field and optional `details` field.
