"""Strict schema validation and projection-shaped dataclasses.

Unknown or malformed API responses raise SchemaError before anything is written
to the index. We never guess field meanings or silently substitute values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .errors import SchemaError


@dataclass(frozen=True)
class AuthorInfo:
    author_id: str
    name: Optional[str]
    url: Optional[str]
    avatar: Optional[str]
    user_title: Optional[str] = None
    user_level: Optional[int] = None


@dataclass(frozen=True)
class CategoryNode:
    category_id: str
    title: str
    parent_id: Optional[str]
    is_container: bool
    url: Optional[str]
    visible_topics_count: Optional[int]
    description: Optional[str]
    node_type: Optional[int]


@dataclass(frozen=True)
class TopicRecord:
    internal_id: str
    public_id: str
    category_id: str
    category_name: str
    category_url: str
    title: str
    content: str
    content_type: str
    author_id: str
    author_name: Optional[str]
    author_url: Optional[str]
    published_at: str
    last_activity_at: str
    reply_count: int
    url: str
    sticky: bool


@dataclass(frozen=True)
class TopicPage:
    topics: List[TopicRecord]
    total: int
    authors: Dict[str, AuthorInfo]


@dataclass(frozen=True)
class ReplyRecord:
    reply_id: str
    public_reply_id: Optional[str]
    parent_reply_id: Optional[str]
    author_id: str
    content: str
    replied_at: str
    url: Optional[str]
    is_best_answer: bool
    highlighted: bool
    attachments: List[Any]
    position: int
    # Number of nested replies the server said exist under this reply
    # (children.metadata.totalCount), or 0. When greater than the number of
    # children actually returned, nested replies are missing from the capture.
    children_expected: int = 0


@dataclass(frozen=True)
class ReplyPage:
    replies: List[ReplyRecord]
    total_count: int
    offset: int
    page_size: int
    has_more: bool
    authors: Dict[str, AuthorInfo]


def _fail(message: str) -> None:
    raise SchemaError(message)


def _dict(value: Any, field_name: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{field_name} must be an object")
    return value


def _list(value: Any, field_name: str) -> list:
    if not isinstance(value, list):
        _fail(f"{field_name} must be a list")
    return value


def _str(value: Any, field_name: str, *, allow_none: bool = False) -> Optional[str]:
    if value is None and allow_none:
        return None
    if not isinstance(value, str):
        _fail(f"{field_name} must be a string")
    return value


def _id(value: Any, field_name: str) -> str:
    if isinstance(value, bool):
        _fail(f"{field_name} must be a string or integer id")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{field_name} must be a non-empty string or integer id")
    if len(value) > 64:
        _fail(f"{field_name} is implausibly long")
    return value.strip()


def _int(value: Any, field_name: str, *, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{field_name} must be an integer")
    if minimum is not None and value < minimum:
        _fail(f"{field_name} must be >= {minimum}")
    return value


def _bool(value: Any, field_name: str, *, default: Optional[bool] = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        _fail(f"{field_name} must be a boolean")
    return value


def _required_any(mapping: dict, keys: tuple, where: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    _fail(f"{where} is missing required field(s): {', '.join(keys)}")


def _validate_authors(raw: Any) -> Dict[str, AuthorInfo]:
    authors_raw = _dict(raw, "authors")
    authors: Dict[str, AuthorInfo] = {}
    for key, value in authors_raw.items():
        where = f"authors[{key}]"
        obj = _dict(value, where)
        author_id = _id(obj.get("id", key), f"{where}.id")
        name = _str(obj.get("name"), f"{where}.name", allow_none=True)
        url = _str(obj.get("url"), f"{where}.url", allow_none=True)
        avatar = _str(obj.get("avatar"), f"{where}.avatar", allow_none=True)
        title = _str(obj.get("userTitle"), f"{where}.userTitle", allow_none=True)
        level = obj.get("userLevel")
        if level is not None:
            level = _int(level, f"{where}.userLevel", minimum=0)
        authors[author_id] = AuthorInfo(author_id, name, url, avatar, title, level)
    return authors


def validate_categories(data: Any) -> List[CategoryNode]:
    root = _dict(data, "category tree")
    nodes: List[CategoryNode] = []
    for key, value in root.items():
        if not str(key).isdigit():
            _fail(f"category tree has unexpected top-level key {key!r}")
        for node in _list(value, f"category tree[{key}]"):
            _walk_category(node, None, nodes, depth=0)
    return nodes


def _walk_category(raw: Any, parent_id: Optional[str], out: List[CategoryNode], depth: int) -> None:
    if depth > 20:
        _fail("category tree is nested too deeply")
    where = "category"
    obj = _dict(raw, where)
    category_id = _id(obj.get("id"), "category.id")
    title = _str(obj.get("title"), f"category[{category_id}].title")
    is_container = _bool(obj.get("isContainer"), f"category[{category_id}].isContainer")
    children = _list(obj.get("children"), f"category[{category_id}].children")
    url = _str(obj.get("url"), f"category[{category_id}].url", allow_none=True)
    description = _str(obj.get("description"), f"category[{category_id}].description", allow_none=True)
    visible = obj.get("visibleTopicsCount")
    if visible is not None:
        visible = _int(visible, f"category[{category_id}].visibleTopicsCount", minimum=0)
    node_type = obj.get("type")
    if node_type is not None:
        node_type = _int(node_type, f"category[{category_id}].type", minimum=0)
    parent_raw = obj.get("parentId")
    node_parent = _id(parent_raw, f"category[{category_id}].parentId") if parent_raw is not None else parent_id
    out.append(
        CategoryNode(
            category_id=category_id,
            title=title or "",
            parent_id=node_parent,
            is_container=is_container,
            url=url,
            visible_topics_count=visible,
            description=description,
            node_type=node_type,
        )
    )
    for child in children:
        _walk_category(child, category_id, out, depth + 1)


def validate_topics(data: Any) -> TopicPage:
    root = _dict(data, "topics response")
    topics_raw = _list(root.get("topics"), "topics")
    total = _int(root.get("total"), "total", minimum=0)
    authors = _validate_authors(root.get("authors"))
    topics: List[TopicRecord] = []
    for raw in topics_raw:
        obj = _dict(raw, "topic")
        public_id = _id(obj.get("publicId"), "topic.publicId")
        where = f"topic[{public_id}]"
        topic_url = _dict(obj.get("topicUrl"), f"{where}.topicUrl")
        destination = _str(topic_url.get("destination"), f"{where}.topicUrl.destination")
        author = _dict(obj.get("author"), f"{where}.author")
        author_id = _id(author.get("id"), f"{where}.author.id")
        author_name = _str(author.get("username"), f"{where}.author.username", allow_none=True)
        topics.append(
            TopicRecord(
                internal_id=_id(obj.get("id"), f"{where}.id"),
                public_id=public_id,
                category_id=_id(obj.get("categoryId"), f"{where}.categoryId"),
                category_name=_str(obj.get("categoryName"), f"{where}.categoryName") or "",
                category_url=_str(obj.get("categoryUrl"), f"{where}.categoryUrl") or "",
                title=_str(obj.get("title"), f"{where}.title") or "",
                content=_str(obj.get("content"), f"{where}.content") or "",
                content_type=_str(obj.get("contentType"), f"{where}.contentType") or "",
                author_id=author_id,
                author_name=author_name,
                author_url=_str(author.get("url"), f"{where}.author.url", allow_none=True),
                published_at=_str(obj.get("publishedAt"), f"{where}.publishedAt") or "",
                last_activity_at=_str(obj.get("lastActivityAt"), f"{where}.lastActivityAt") or "",
                reply_count=_int(obj.get("replyCount"), f"{where}.replyCount", minimum=0),
                url=destination or "",
                sticky=_bool(obj.get("sticky"), f"{where}.sticky", default=False),
            )
        )
    return TopicPage(topics=topics, total=total, authors=authors)


def validate_replies(data: Any) -> ReplyPage:
    root = _dict(data, "replies response")
    replies_obj = _dict(root.get("replies"), "replies")
    result = _list(replies_obj.get("result"), "replies.result")
    metadata = _dict(replies_obj.get("metadata"), "replies.metadata")
    authors = _validate_authors(root.get("authors"))
    total_count = _int(metadata.get("totalCount"), "replies.metadata.totalCount", minimum=0)
    offset = _int(metadata.get("offset"), "replies.metadata.offset", minimum=0)
    page_size = _int(metadata.get("pageSize"), "replies.metadata.pageSize", minimum=1)
    has_more = _bool(metadata.get("hasMore"), "replies.metadata.hasMore")
    flat: List[ReplyRecord] = []
    position = 0
    for raw in result:
        position = _walk_reply(raw, None, flat, position)
    return ReplyPage(flat, total_count, offset, page_size, has_more, authors)


def _walk_reply(raw: Any, parent_id: Optional[str], out: List[ReplyRecord], position: int) -> int:
    obj = _dict(raw, "reply")
    reply_id = _id(obj.get("id"), "reply.id")
    where = f"reply[{reply_id}]"
    public_id = obj.get("publicReplyId")
    public_id = _id(public_id, f"{where}.publicReplyId") if public_id is not None else None
    attachments = obj.get("attachments", [])
    attachments = _list(attachments, f"{where}.attachments")
    children = obj.get("children")
    child_items: List[Any] = []
    children_expected = 0
    if children is not None:
        if isinstance(children, list):
            child_items = children
            children_expected = len(children)
        elif isinstance(children, dict):
            child_items = _list(children.get("result"), f"{where}.children.result")
            meta = children.get("metadata")
            if meta is not None:
                meta_obj = _dict(meta, f"{where}.children.metadata")
                total = meta_obj.get("totalCount")
                children_expected = (
                    _int(total, f"{where}.children.metadata.totalCount", minimum=0)
                    if total is not None
                    else len(child_items)
                )
            else:
                children_expected = len(child_items)
        else:
            _fail(f"{where}.children must be an object, a list, or null")
    out.append(
        ReplyRecord(
            reply_id=reply_id,
            public_reply_id=public_id,
            parent_reply_id=parent_id,
            author_id=_id(obj.get("authorId"), f"{where}.authorId"),
            content=_str(obj.get("content"), f"{where}.content") or "",
            replied_at=_str(obj.get("repliedAt"), f"{where}.repliedAt") or "",
            url=_str(obj.get("url"), f"{where}.url", allow_none=True),
            is_best_answer=_bool(obj.get("isBestAnswer"), f"{where}.isBestAnswer", default=False),
            highlighted=_bool(obj.get("highlighted"), f"{where}.highlighted", default=False),
            attachments=attachments,
            position=position,
            children_expected=children_expected,
        )
    )
    position += 1
    for child in child_items:
        position = _walk_reply(child, reply_id, out, position)
    return position
