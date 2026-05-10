import { useRef, useState } from "react";
import Webcam from "react-webcam";
import "./App.css";

const API_URL = "http://127.0.0.1:5001/api/analyze";

function dataUrlToBlob(dataUrl) {
  const parts = dataUrl.split(",");
  if (parts.length !== 2) {
    throw new Error("Invalid captured image data.");
  }

  const mimeMatch = parts[0].match(/data:(.*?);base64/);
  const mimeType = mimeMatch ? mimeMatch[1] : "image/jpeg";
  const binary = atob(parts[1]);
  const bytes = new Uint8Array(binary.length);

  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }

  return new Blob([bytes], { type: mimeType });
}

function App() {
  const webcamRef = useRef(null);

  const [command, setCommand] = useState("pick up the red bottle");
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (event) => {
    event.preventDefault();
    setError("");

    const trimmedCommand = command.trim();
    if (!trimmedCommand) {
      setError("Please enter a command.");
      return;
    }

    const screenshot = webcamRef.current?.getScreenshot();
    if (!screenshot) {
      setError("Could not capture webcam frame. Check camera permissions.");
      return;
    }

    setLoading(true);
    setResult(null);

    try {
      const imageBlob = dataUrlToBlob(screenshot);
      const formData = new FormData();
      formData.append("image", imageBlob, "capture.jpg");
      formData.append("command", trimmedCommand);

      const response = await fetch(API_URL, {
        method: "POST",
        body: formData,
      });

      const payload = await response.json().catch(() => null);

      if (!response.ok) {
        const message = payload?.details || payload?.error || "Request failed.";
        throw new Error(message);
      }

      setResult(payload);
    } catch (submitError) {
      setError(submitError.message || "Failed to analyze image.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="app">
      <h1>Robot Vision Control</h1>

      <div className="webcam-panel">
        <div className="webcam-wrap">
          <Webcam
            ref={webcamRef}
            audio={false}
            screenshotFormat="image/jpeg"
            className="webcam"
            videoConstraints={{ facingMode: "environment" }}
          />

          {result && (
            <div
              className="crosshair"
              style={{ left: `${result.x}%`, top: `${result.y}%` }}
              aria-hidden="true"
            >
              <span className="crosshair-h" />
              <span className="crosshair-v" />
              <span className="crosshair-dot" />
            </div>
          )}
        </div>
      </div>

      <form className="controls" onSubmit={handleSubmit}>
        <label htmlFor="command">Command</label>
        <input
          id="command"
          type="text"
          value={command}
          onChange={(event) => setCommand(event.target.value)}
          placeholder="pick up the red bottle"
        />

        <button type="submit" disabled={loading}>
          {loading ? "Analyzing..." : "Submit"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {result && (
        <section className="result">
          <h2>Analysis Result</h2>
          <p>
            <strong>Detected object:</strong> {result.object}
          </p>
          <p>
            <strong>Action:</strong> {result.action}
          </p>
          <p>
            <strong>X:</strong> {result.x}
          </p>
          <p>
            <strong>Y:</strong> {result.y}
          </p>
          <p>
            <strong>Confidence:</strong> {result.confidence}
          </p>
        </section>
      )}
    </main>
  );
}

export default App;
