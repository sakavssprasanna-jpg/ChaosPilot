import io
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image

from multimodal.schema import VisualInspectionResult, utc_now_iso

def discover_vision_backend() -> Dict[str, Any]:
    """Inspects installed packages and environment credentials to determine available vision capabilities."""
    has_gemini_key = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))

    genai_installed = False
    try:
        import google.genai
        genai_installed = True
    except ImportError:
        try:
            import google.generativeai
            genai_installed = True
        except ImportError:
            pass

    if has_gemini_key and genai_installed:
        return {
            "backend": "gemini",
            "available": True,
            "details": "Gemini multimodal vision API configured with credentials",
            "local_capabilities": ["local_structural", "pillow"]
        }
    else:
        reason = "GEMINI_API_KEY or GOOGLE_API_KEY not configured in environment" if not has_gemini_key else "google-genai SDK not installed"
        return {
            "backend": "none",
            "available": False,
            "details": f"No multimodal vision backend configured ({reason})",
            "local_capabilities": ["local_structural", "pillow"]
        }

class VisionInspector:
    """Vision inspection module for validating and analyzing incident visual evidence."""

    def __init__(self, backend: Optional[str] = None):
        discovery = discover_vision_backend()
        if backend is None:
            self.backend = discovery["backend"]
        else:
            self.backend = backend

    def validate_image(
        self,
        image_input: Union[str, Path, bytes, Image.Image]
    ) -> Tuple[bool, Optional[str], Optional[Image.Image]]:
        """Validates image existence, readability, and format integrity."""
        try:
            if isinstance(image_input, Image.Image):
                return True, None, image_input

            if isinstance(image_input, (str, Path)):
                path = Path(image_input)
                if not path.exists():
                    return False, f"Image file not found: {path}", None
                if path.stat().st_size == 0:
                    return False, f"Image file is empty (0 bytes): {path}", None

                # Test open and load to catch truncated or corrupted files
                with open(path, "rb") as f:
                    data = f.read()
                img = Image.open(io.BytesIO(data))
                img.load()
                return True, None, img

            if isinstance(image_input, bytes):
                if len(image_input) == 0:
                    return False, "Empty image bytes provided", None
                img = Image.open(io.BytesIO(image_input))
                img.load()
                return True, None, img

            return False, f"Unsupported image input type: {type(image_input).__name__}", None

        except Exception as e:
            return False, f"Invalid image format or corrupt image data: {e}", None

    def inspect(
        self,
        image_input: Union[str, Path, bytes, Image.Image],
        prompt: Optional[str] = None
    ) -> VisualInspectionResult:
        """Inspects an image and returns structured visual evidence without fabricated observations."""
        source_name = str(image_input) if isinstance(image_input, (str, Path)) else "in_memory_image"
        img_id = f"img_{int(time.time() * 1000)}"

        # 1. Image validation
        is_valid, error_msg, img = self.validate_image(image_input)
        if not is_valid or img is None:
            return VisualInspectionResult(
                image_id=img_id,
                source=source_name,
                timestamp=utc_now_iso(),
                observations=[],
                status="error",
                backend=self.backend,
                metadata={},
                error=error_msg
            )

        # 2. Handle unavailable backend
        if self.backend == "none":
            return VisualInspectionResult(
                image_id=img_id,
                source=source_name,
                timestamp=utc_now_iso(),
                observations=[],
                status="unavailable",
                backend="none",
                metadata={"dimensions": f"{img.width}x{img.height}", "format": img.format or "unknown"},
                error="Vision backend unavailable: GEMINI_API_KEY or GOOGLE_API_KEY not configured. No visual model ran."
            )

        # 3. Handle Gemini multimodal backend if configured
        if self.backend == "gemini":
            has_gemini_key = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
            if not has_gemini_key:
                return VisualInspectionResult(
                    image_id=img_id,
                    source=source_name,
                    timestamp=utc_now_iso(),
                    observations=[],
                    status="unavailable",
                    backend="gemini",
                    metadata={"dimensions": f"{img.width}x{img.height}", "format": img.format or "unknown"},
                    error="Vision backend unavailable: GEMINI_API_KEY or GOOGLE_API_KEY not configured. No visual model ran."
                )

            try:
                # Use official google-genai SDK if available
                from google import genai
                client = genai.Client()
                effective_prompt = prompt or "Analyze this incident telemetry/architecture diagram and list key observations."
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[img, effective_prompt]
                )
                observations = [line.strip() for line in response.text.split("\n") if line.strip()]
                return VisualInspectionResult(
                    image_id=img_id,
                    source=source_name,
                    timestamp=utc_now_iso(),
                    observations=observations,
                    status="success",
                    backend="gemini",
                    metadata={"model": "gemini-2.5-flash", "dimensions": f"{img.width}x{img.height}"},
                    error=None
                )
            except Exception as e:
                return VisualInspectionResult(
                    image_id=img_id,
                    source=source_name,
                    timestamp=utc_now_iso(),
                    observations=[],
                    status="error",
                    backend="gemini",
                    metadata={"dimensions": f"{img.width}x{img.height}"},
                    error=f"Gemini vision inspection failed: {e}"
                )

        # 4. Handle genuine local structural analysis
        if self.backend == "local_structural":
            aspect_ratio = round(img.width / img.height, 2) if img.height > 0 else 0.0
            fmt = img.format or "PNG"
            observations = [
                f"Image format: {fmt}, Resolution: {img.width}x{img.height}, Color mode: {img.mode}",
                f"Aspect ratio: {aspect_ratio:.2f}",
                f"Image dimensions verified: width={img.width}px, height={img.height}px"
            ]
            metadata = {
                "format": fmt,
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "aspect_ratio": aspect_ratio
            }
            return VisualInspectionResult(
                image_id=img_id,
                source=source_name,
                timestamp=utc_now_iso(),
                observations=observations,
                status="success",
                backend="local_structural",
                metadata=metadata,
                error=None
            )

        # Any other backend
        return VisualInspectionResult(
            image_id=img_id,
            source=source_name,
            timestamp=utc_now_iso(),
            observations=[],
            status="unavailable",
            backend=self.backend,
            metadata={},
            error=f"Unrecognized vision backend: '{self.backend}'"
        )
