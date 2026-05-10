import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import axios from "axios";
import multer from "multer";

dotenv.config();

const app = express();
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 10 * 1024 * 1024 },
});

const PORT = Number(process.env.PORT) || 5001;
const FLASK_ANALYZE_URL =
  process.env.FLASK_ANALYZE_URL || "http://127.0.0.1:5000/analyze";

app.use(cors());

app.get("/api/health", (_req, res) => {
  return res.status(200).json({ status: "ok" });
});

app.post("/api/analyze", upload.single("image"), async (req, res) => {
  try {
    const command = req.body?.command;
    const file = req.file;

    if (!command || typeof command !== "string" || !command.trim()) {
      return res.status(400).json({
        error: "invalid_command",
        details: "Field 'command' is required and must be a non-empty string.",
      });
    }

    if (!file || !file.buffer) {
      return res.status(400).json({
        error: "missing_image",
        details: "Multipart field 'image' is required.",
      });
    }

    const imageBase64 = file.buffer.toString("base64");

    const flaskResponse = await axios.post(
      FLASK_ANALYZE_URL,
      {
        image: imageBase64,
        command: command.trim(),
      },
      {
        timeout: 30000,
        headers: { "Content-Type": "application/json" },
      },
    );

    return res.status(flaskResponse.status).json(flaskResponse.data);
  } catch (error) {
    if (axios.isAxiosError(error)) {
      if (error.response) {
        return res.status(error.response.status).json(
          error.response.data || {
            error: "flask_error",
            details: "Flask API returned an error response.",
          },
        );
      }

      if (error.code === "ECONNABORTED") {
        return res.status(504).json({
          error: "timeout",
          details: "Request to Flask API timed out.",
        });
      }

      return res.status(502).json({
        error: "flask_unreachable",
        details: "Could not reach Flask API at configured URL.",
      });
    }

    return res.status(500).json({
      error: "internal_error",
      details: "Unexpected server error while processing analyze request.",
    });
  }
});

app.use((err, _req, res, _next) => {
  if (err instanceof multer.MulterError) {
    if (err.code === "LIMIT_FILE_SIZE") {
      return res.status(413).json({
        error: "file_too_large",
        details: "Image exceeds maximum allowed size of 10MB.",
      });
    }
    return res.status(400).json({
      error: "upload_error",
      details: err.message,
    });
  }

  return res.status(500).json({
    error: "internal_error",
    details: "Unexpected server error.",
  });
});

app.listen(PORT, () => {
  console.log(`Express API listening on http://127.0.0.1:${PORT}`);
  console.log(
    `Forwarding /api/analyze to Flask endpoint: ${FLASK_ANALYZE_URL}`,
  );
});
