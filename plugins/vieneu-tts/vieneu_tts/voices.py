"""VieNeu-TTS v3 Turbo preset voices: (id, region, style, gender). The first is the default."""

VOICES = [
    ("Hải Đăng", "North", "natural", "male"),
    ("Adam bựa", "North", "natural", "male"),
    ("Trúc Ly", "North", "natural", "female"),
    ("Thiện Minh", "North", "storytelling", "male"),
    ("Mai Anh", "North", "news", "female"),
    ("Thùy Dung", "South", "news", "female"),
    ("Thiền Tâm Đức", "North", "storytelling", "male"),
    ("Ngọc Huyền", "North", "natural", "female"),
    ("Quang Sơn", "Central", "natural", "male"),
    ("Ngọc Trân", "Central", "natural", "female"),
    ("Minh Đức", "North", "news", "male"),
    ("Phạm Tuyên", "North", "natural", "male"),
    ("Thái Sơn", "South", "storytelling", "male"),
    ("Xuân Vĩnh", "North", "natural", "male"),
    ("Thanh Bình", "North", "storytelling", "male"),
    ("Ngọc Linh", "North", "storytelling", "female"),
    ("Đoan Trang", "North", "natural", "female"),
    ("Thục Đoan", "South", "storytelling", "female"),
    ("Minh Triết", "South", "news", "male"),
    ("Mỹ Duyên", "South", "audiobook", "female"),
    ("Quỳnh Anh", "North", "audiobook", "female"),
    ("Đức Trí", "South", "audiobook", "male"),
    ("Kim Thanh", "South", "audiobook", "female"),
    ("Adam", "South", "natural", "male"),
    ("Quốc Tuấn", "North", "natural", "male"),
]
DEFAULT_VOICE = VOICES[0][0]
NAMES = [v[0] for v in VOICES]


def voice_list():
    return [{"id": i, "language": "vi", "region": r, "style": s, "gender": g} for i, r, s, g in VOICES]


def find_voice(name):
    """The preset whose id matches `name`, ignoring case; None when there is none."""
    wanted = (name or "").strip().casefold()
    return next((n for n in NAMES if n.casefold() == wanted), None)
