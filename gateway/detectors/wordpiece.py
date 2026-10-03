"""
BERT WordPiece tokenizer (uncased), pure Python, for serving the distilled guard without the `tokenizers` package.

Why not `tokenizers`: it depends on huggingface-hub and, through it, a dozen more packages, to do something that is a page of code. This repository hash-locks every dependency and keeps the 512 MB
image small, so the runtime dependencies of the student layer are `onnxruntime` and numpy and nothing else.

The algorithm is the one in BERT's reference tokenizer (BasicTokenizer + WordpieceTokenizer, do_lower_case=True, tokenize_chinese_chars=True):
  1. clean: drop NUL, U+FFFD and control characters; every other whitespace character becomes a space;
  2. put spaces around CJK ideographs;
  3. split on whitespace; lower-case each token, strip accents (NFD, drop combining marks) and split it on punctuation;
  4. WordPiece, greedy longest match first, continuation pieces prefixed `##`; a word longer than 100 characters, or one with no valid segmentation, is a single [UNK];
  5. [CLS] ... [SEP], truncated so the whole sequence is at most `max_length`.

One deliberate difference from the Hugging Face fast tokenizer: text that happens to read `[SEP]`, `[CLS]`, `[MASK]` is NOT turned into the special token (the fast tokenizer does that by default,
which lets a prompt smuggle structural tokens into the model's input); here it is tokenized as the plain characters it is. Everywhere else the ids are identical.
The export script checks its output against the reference tokenizer for every text in the project's data.
"""
import json
import unicodedata
from pathlib import Path

MAX_WORD_CHARS = 100


def _is_whitespace(ch: str) -> bool:
    return ch in " \t\n\r" or unicodedata.category(ch) == "Zs"


def _is_control(ch: str) -> bool:
    if ch in "\t\n\r":
        return False
    return unicodedata.category(ch).startswith("C")


def _is_punctuation(ch: str) -> bool:
    cp = ord(ch)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(ch).startswith("P")


def _is_cjk(cp: int) -> bool:
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2A6DF or 0x2A700 <= cp <= 0x2B73F or 0x2B740 <= cp <= 0x2B81F
            or 0x2B820 <= cp <= 0x2CEAF or 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F)


def _clean(text: str) -> str:
    out = []
    for ch in text:
        cp = ord(ch)
        if cp == 0 or cp == 0xFFFD or _is_control(ch):
            continue
        out.append(" " if _is_whitespace(ch) else ch)
    return "".join(out)


def _space_cjk(text: str) -> str:
    out = []
    for ch in text:
        if _is_cjk(ord(ch)):
            out.extend((" ", ch, " "))
        else:
            out.append(ch)
    return "".join(out)


def _strip_accents(token: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", token) if unicodedata.category(ch) != "Mn")


def _split_punctuation(token: str) -> list[str]:
    pieces, current = [], []
    for ch in token:
        if _is_punctuation(ch):
            if current:
                pieces.append("".join(current))
                current = []
            pieces.append(ch)
        else:
            current.append(ch)
    if current:
        pieces.append("".join(current))
    return pieces


class WordPieceTokenizer:
    def __init__(self, vocab: dict[str, int]):
        self.vocab = vocab
        self.unk, self.cls, self.sep = vocab["[UNK]"], vocab["[CLS]"], vocab["[SEP]"]

    @classmethod
    def from_file(cls, path: Path) -> "WordPieceTokenizer":
        tokens = Path(path).read_text(encoding="utf-8").split("\n")
        if tokens and tokens[-1] == "":
            tokens.pop()
        return cls({tok: i for i, tok in enumerate(tokens)})

    @classmethod
    def from_tokenizer_json(cls, path: Path) -> "WordPieceTokenizer":
        """Load the WordPiece vocabulary from a Hugging Face tokenizer.json."""
        tokenizer = json.loads(Path(path).read_text(encoding="utf-8"))
        model = tokenizer.get("model")
        if not isinstance(model, dict) or model.get("type") != "WordPiece":
            raise ValueError(f"{path} does not contain a WordPiece tokenizer")
        normalizer = tokenizer.get("normalizer")
        if not isinstance(normalizer, dict) or normalizer.get("type") != "BertNormalizer":
            raise ValueError(f"{path} does not use the supported BERT normalizer")
        if not (
            normalizer.get("clean_text") is True
            and normalizer.get("handle_chinese_chars") is True
            and normalizer.get("lowercase") is True
            and (normalizer.get("strip_accents") is None or normalizer.get("strip_accents") is True)
        ):
            raise ValueError(f"{path} has unsupported BERT normalizer settings")
        pre_tokenizer = tokenizer.get("pre_tokenizer")
        if not isinstance(pre_tokenizer, dict) or pre_tokenizer.get("type") != "BertPreTokenizer":
            raise ValueError(f"{path} does not use the supported BERT pre-tokenizer")
        if model.get("continuing_subword_prefix") != "##" or model.get("unk_token") != "[UNK]":
            raise ValueError(f"{path} has unsupported WordPiece settings")
        if model.get("max_input_chars_per_word", MAX_WORD_CHARS) != MAX_WORD_CHARS:
            raise ValueError(f"{path} has unsupported WordPiece maximum word length")
        vocab = model.get("vocab")
        if not isinstance(vocab, dict) or not vocab or any(
            not isinstance(token, str) or not isinstance(token_id, int) or isinstance(token_id, bool)
            for token, token_id in vocab.items()
        ):
            raise ValueError(f"{path} has an invalid WordPiece vocabulary")
        ids = set(vocab.values())
        if ids != set(range(len(vocab))):
            raise ValueError(f"{path} WordPiece vocabulary IDs must be contiguous from zero")
        required = {"[UNK]", "[CLS]", "[SEP]"}
        if not required.issubset(vocab):
            raise ValueError(f"{path} WordPiece vocabulary is missing {sorted(required - set(vocab))}")
        return cls(vocab)

    def _wordpiece(self, word: str) -> list[int]:
        if len(word) > MAX_WORD_CHARS:
            return [self.unk]
        ids, start = [], 0
        while start < len(word):
            end, found = len(word), None
            while start < end:
                piece = word[start:end]
                if start > 0:
                    piece = "##" + piece
                if piece in self.vocab:
                    found = self.vocab[piece]
                    break
                end -= 1
            if found is None:
                return [self.unk]
            ids.append(found)
            start = end
        return ids

    def tokenize(self, text: str) -> list[int]:
        ids: list[int] = []
        for word in _space_cjk(_clean(text)).split():
            for piece in _split_punctuation(_strip_accents(word.lower())):
                ids.extend(self._wordpiece(piece))
        return ids

    def encode(self, text: str, max_length: int = 256) -> list[int]:
        room = max_length - 2
        ids: list[int] = []
        for word in _space_cjk(_clean(text)).split():
            for piece in _split_punctuation(_strip_accents(word.lower())):
                ids.extend(self._wordpiece(piece))
            if len(ids) >= room:                       # the rest of a long input would be cut off anyway
                break
        return [self.cls, *ids[:room], self.sep]
