"""한 사람씩 촬영한 얼굴의 구도 보정과 AI 의상 합성."""

from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
from threading import Lock

import cv2
import numpy as np


PORTRAIT_SIZE = (680, 980)
PORTRAIT_ASPECT = PORTRAIT_SIZE[0] / PORTRAIT_SIZE[1]
PORTRAIT_PROMPT_SUFFIX = (
    "For this photo booth mode, make a clean studio identification-style portrait "
    "in a vertical 34:49 composition. Replace any setting specified earlier with "
    "a plain light gray studio backdrop. Keep the one person's original head "
    "position, size and angle and the exact locations of their eyes, nose and mouth. "
    "Keep their appearance and expression. Change only the clothing, requested "
    "accessories and background. Keep the chosen outfit visible, use soft even "
    "front lighting, and leave comfortable margins around the head. "
    "No text, markings, logos, simulated ID documents or other people."
)

_INPUT_SIZE = 640
_DETECTOR_LOCK = Lock()
_ALIGNMENT_ERROR = "변환된 얼굴 위치가 달라요. 이 컷을 다시 찍어 주세요."


@dataclass(frozen=True)
class PreparedPortrait:
    image_bgr: np.ndarray
    face_box: tuple[int, int, int, int]
    landmarks: np.ndarray


@lru_cache(maxsize=1)
def _detector():
    # YuNet v2's output layers require OpenCV 4.8+. Bookworm's 4.6 uses v1.
    version = tuple(int(part) for part in cv2.__version__.split(".")[:2])
    revision, threshold = ("2022mar", 0.68) if version < (4, 8) else ("2023mar", 0.78)
    model = Path(__file__).resolve().parent / "assets" / "yunet" / f"face_detection_yunet_{revision}.onnx"
    if not model.is_file():
        raise ValueError("얼굴 검출 모델 파일이 없어요. 운영자에게 알려 주세요.")
    try:
        return cv2.FaceDetectorYN.create(str(model), "", (_INPUT_SIZE, _INPUT_SIZE), threshold, 0.3, 100)
    except (AttributeError, cv2.error) as error:
        raise ValueError("얼굴 검출 모델을 열 수 없어요. 운영자에게 알려 주세요.") from error


def _detect_faces(image: np.ndarray) -> list[np.ndarray]:
    height, width = image.shape[:2]
    scale = _INPUT_SIZE / max(height, width)
    resized_width = max(1, round(width * scale))
    resized_height = max(1, round(height * scale))
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
    padded = np.zeros((_INPUT_SIZE, _INPUT_SIZE, 3), dtype=np.uint8)
    padded[:resized_height, :resized_width] = resized
    try:
        with _DETECTOR_LOCK:
            _, faces = _detector().detect(padded)
    except cv2.error as error:
        raise ValueError("얼굴 검출을 실행할 수 없어요. 운영자에게 알려 주세요.") from error
    if faces is None:
        return []
    inside_frame = []
    for face in faces:
        x, y, face_width, face_height = face[:4]
        # Discard detections that extend past the actual image into the padding.
        if (
            np.all(np.isfinite(face)) and 0 <= x < x + face_width <= resized_width
            and 0 <= y < y + face_height <= resized_height
        ):
            inside_frame.append(face[:14] / scale)
    return inside_frame


def _require_image(image: np.ndarray, message: str) -> None:
    if (
        not isinstance(image, np.ndarray) or image.ndim != 3 or image.shape[2] != 3
        or image.dtype != np.uint8 or min(image.shape[:2]) < 64
    ):
        raise ValueError(message)


def _landmarks(face: np.ndarray) -> np.ndarray:
    # YuNet supplies both eyes, the nose, and both corners of the mouth.
    marks = face[4:14].reshape(5, 2).copy()
    marks[:2] = marks[:2][np.argsort(marks[:2, 0])]
    marks[3:] = marks[3:][np.argsort(marks[3:, 0])]
    return marks


def _eyes(points: np.ndarray) -> tuple[np.ndarray, float, float]:
    delta = points[1] - points[0]
    return (points[0] + points[1]) / 2, float(np.linalg.norm(delta)), math.degrees(math.atan2(delta[1], delta[0]))


def prepare_portrait(frame_bgr: np.ndarray) -> PreparedPortrait:
    """한 얼굴을 검출하고 머리와 상반신이 들어가도록 34:49로 중앙 정렬합니다."""
    _require_image(frame_bgr, "사진을 읽을 수 없어요. 다시 찍어 주세요.")
    faces = _detect_faces(frame_bgr)
    if not faces:
        raise ValueError("얼굴을 찾지 못했어요. 화면 가운데에서 정면을 봐 주세요.")
    if len(faces) != 1:
        raise ValueError("사진에는 한 명만 들어와 주세요.")
    face = faces[0]
    x, y, width, height = (float(value) for value in face[:4])
    if min(width, height) < 65:
        raise ValueError("얼굴이 너무 작아요. 카메라에 조금 더 가까이 와 주세요.")

    points = _landmarks(face)
    eye_center, eye_span, eye_angle = _eyes(points)
    nose = points[2]
    if (
        abs(eye_angle) > 12 or eye_span < width * 0.23 or abs(nose[0] - eye_center[0]) > eye_span * 0.27
        or not eye_center[1] < nose[1] < points[3:, 1].mean()
    ):
        raise ValueError("고개를 바로 하고 카메라를 정면으로 바라봐 주세요.")

    crop_height = max(height / 0.44, width / (0.64 * PORTRAIT_ASPECT))
    crop_width = crop_height * PORTRAIT_ASPECT
    frame_height, frame_width = frame_bgr.shape[:2]
    if crop_width > frame_width or crop_height > frame_height:
        raise ValueError("얼굴이 너무 가까워요. 조금 뒤로 물러서 주세요.")
    desired_left = x + width / 2 - crop_width / 2
    desired_top = y + height / 2 - crop_height * 0.44
    left = min(max(0, desired_left), frame_width - crop_width)
    top = min(max(0, desired_top), frame_height - crop_height)
    if abs(left - desired_left) > crop_width * 0.06 or abs(top - desired_top) > crop_height * 0.06:
        raise ValueError("얼굴을 화면 가운데로 옮겨 주세요.")

    scale = PORTRAIT_SIZE[1] / crop_height
    to_portrait = np.array([[scale, 0, -left * scale], [0, scale, -top * scale]])
    image = cv2.warpAffine(frame_bgr, to_portrait, PORTRAIT_SIZE, borderMode=cv2.BORDER_REPLICATE)
    x, y = (x - left) * scale, (y - top) * scale
    box = (round(x), round(y), round(width * scale), round(height * scale))
    return PreparedPortrait(image, box, (points - (left, top)) * scale)


def _normalize_result(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    crop_width = min(width, round(height * PORTRAIT_ASPECT))
    crop_height = min(height, round(width / PORTRAIT_ASPECT))
    x, y = (width - crop_width) // 2, (height - crop_height) // 2
    return cv2.resize(image[y:y + crop_height, x:x + crop_width], PORTRAIT_SIZE)


def _align_face(prepared: PreparedPortrait, face: np.ndarray) -> np.ndarray:
    source_x, source_y, source_width, source_height = prepared.face_box
    x, y, width, height = face[:4]
    if (
        abs(x + width / 2 - source_x - source_width / 2) > source_width * 0.22
        or abs(y + height / 2 - source_y - source_height / 2) > source_height * 0.22
        or not 0.80 <= width / source_width <= 1.25
        or not 0.80 <= height / source_height <= 1.25
    ):
        raise ValueError(_ALIGNMENT_ERROR)

    target_marks = _landmarks(face)
    source_center, source_span, source_angle = _eyes(prepared.landmarks)
    center, span, angle = _eyes(target_marks)
    scale = span / source_span
    rotation = source_angle - angle
    if not 0.82 <= scale <= 1.22 or abs(rotation) > 8:
        raise ValueError(_ALIGNMENT_ERROR)

    matrix = cv2.getRotationMatrix2D(tuple(source_center), rotation, scale)
    matrix[:, 2] += center - source_center
    aligned = prepared.landmarks @ matrix[:, :2].T + matrix[:, 2]
    if np.linalg.norm(aligned[2:] - target_marks[2:], axis=1).max() > span * 0.21:
        raise ValueError(_ALIGNMENT_ERROR)
    return matrix


def finalize_portrait(prepared: PreparedPortrait, transformed_bgr: np.ndarray) -> np.ndarray:
    """얼굴 위치를 확인하고 원본 눈·코·입을 포함한 얼굴 영역을 의상과 자연스럽게 합칩니다."""
    _require_image(transformed_bgr, "AI 사진을 읽을 수 없어요. 이 컷을 다시 찍어 주세요.")
    target = _normalize_result(transformed_bgr)
    faces = _detect_faces(target)
    if len(faces) != 1:
        raise ValueError(_ALIGNMENT_ERROR)
    matrix = _align_face(prepared, faces[0])

    x, y, width, height = prepared.face_box
    mask = np.zeros(prepared.image_bgr.shape[:2], dtype=np.uint8)
    center = (round(x + width * 0.50), round(y + height * 0.52))
    axes = (round(width * 0.42), round(height * 0.45))
    cv2.ellipse(mask, center, axes, 0, 0, 360, 255, thickness=-1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=width * 0.035)
    alpha = cv2.warpAffine(mask, matrix, PORTRAIT_SIZE)[:, :, None].astype(np.float32) / 255
    original = cv2.warpAffine(prepared.image_bgr, matrix, PORTRAIT_SIZE)
    return np.rint(alpha * original + (1 - alpha) * target).astype(np.uint8)
