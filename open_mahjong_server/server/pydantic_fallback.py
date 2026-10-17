"""极简 ``pydantic`` 兼容层（纯标准库）。

用途：单机版把杭州引擎打进 APK（Chaquopy）时，官方源没有 pydantic v2 /
pydantic-core 的 Android 预编译 wheel。``server/response.py`` 只在导入期需要
``BaseModel`` / ``Field`` / 三个装饰器；杭州单机链路实际发送的是原生 dict
（见 ``game_hangzhou/runtime.py`` 的 ``_send_timed_payload``），因此这里只需
提供一个行为足够接近的替身，让 response.py 可被导入且 ``model_dump`` 可用。

线上环境始终优先使用真实 pydantic（``server/response.py`` 里 try/except 已处理），
本模块只在 ``import pydantic`` 失败时兜底，因此不会影响正式服务器行为。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_UNDEFINED = object()


class FieldInfo:
    __slots__ = ("default", "default_factory", "kwargs")

    def __init__(self, default=_UNDEFINED, *, default_factory=None, **kwargs):
        self.default = default
        self.default_factory = default_factory
        self.kwargs = kwargs


def Field(default=_UNDEFINED, *, default_factory=None, **kwargs):  # noqa: N802
    """对应 pydantic.Field：只保留默认值 / 默认工厂，其余参数原样忽略。"""
    return FieldInfo(default, default_factory=default_factory, **kwargs)


# ---------------------------------------------------------------------------
# 装饰器：把元数据挂在函数上，交给元类收集
# ---------------------------------------------------------------------------
def field_serializer(*fields, when_used="always", **kwargs):
    def deco(fn):
        fn.__field_serializer__ = {"fields": fields, "when_used": when_used}
        return fn
    return deco


def field_validator(*fields, mode="after", **kwargs):
    def deco(fn):
        target = fn.__func__ if isinstance(fn, classmethod) else fn
        target.__field_validator__ = {"fields": fields, "mode": mode}
        return fn
    return deco


def model_serializer(fn=None, *, mode="wrap", **kwargs):
    def deco(f):
        f.__model_serializer__ = {"mode": mode}
        return f
    if fn is not None:
        return deco(fn)
    return deco


def _plain(value, exclude_none, mode):
    if isinstance(value, BaseModel):
        return value.model_dump(exclude_none=exclude_none, mode=mode)
    if isinstance(value, dict):
        return {k: _plain(v, exclude_none, mode) for k, v in value.items()
                if not (exclude_none and v is None)}
    if isinstance(value, (list, tuple)):
        return [_plain(v, exclude_none, mode) for v in value]
    return value


class _ModelMeta(type):
    def __new__(mcls, name, bases, namespace):
        cls = super().__new__(mcls, name, bases, namespace)

        fields: Dict[str, FieldInfo] = {}
        order: List[str] = []
        serializers: Dict[str, tuple] = {}
        validators: Dict[str, List[Any]] = {}
        model_ser = None

        # 先继承基类字段
        for base in reversed(cls.__mro__[1:]):
            base_fields = getattr(base, "__model_fields__", None)
            if base_fields:
                for fname, info in base_fields.items():
                    if fname not in fields:
                        order.append(fname)
                    fields[fname] = info
            serializers.update(getattr(base, "__field_serializers__", {}) or {})
            for fname, fns in (getattr(base, "__field_before_validators__", {}) or {}).items():
                validators.setdefault(fname, []).extend(fns)
            if getattr(base, "__model_serializer_fn__", None) is not None:
                model_ser = base.__model_serializer_fn__

        # 先扫描装饰器（序列化器 / 校验器 / 模型序列化器）
        for key, value in list(namespace.items()):
            fn = value.__func__ if isinstance(value, classmethod) else value

            if hasattr(fn, "__field_serializer__"):
                meta = fn.__field_serializer__
                for fname in meta["fields"]:
                    serializers[fname] = (fn, meta["when_used"])
            if hasattr(fn, "__field_validator__"):
                meta = fn.__field_validator__
                for fname in meta["fields"]:
                    validators.setdefault(fname, []).append(fn)
            if hasattr(fn, "__model_serializer__"):
                model_ser = fn

        # 再按注解声明顺序收集模型字段：pydantic 字段由注解决定，
        # 仅有注解而无默认值的字段（如 rank: int）不会出现在 namespace 里。
        # Python 3.14 起类注解改为惰性求值（PEP 649），namespace 中不再有
        # ``__annotations__``，需通过 ``cls.__annotations__`` 读取，兼容 3.8+。
        own_annotations = getattr(cls, "__annotations__", None) or {}
        for key in own_annotations:
            if key.startswith("_"):        # 跳过内部机件（__model_fields__ 等）
                continue
            if key not in fields:
                order.append(key)
            value = namespace.get(key, _UNDEFINED)
            if isinstance(value, FieldInfo):
                fields[key] = value
            else:
                # 带注解的类属性即为默认值；无默认值则必填
                fields[key] = FieldInfo() if value is _UNDEFINED else FieldInfo(value)

        cls.__model_fields__ = fields
        cls.__fields_order__ = order
        cls.__field_serializers__ = serializers
        cls.__field_before_validators__ = validators
        cls.__model_serializer_fn__ = model_ser
        cls.model_fields = fields
        return cls


class BaseModel(metaclass=_ModelMeta):
    __model_fields__: Dict[str, FieldInfo] = {}
    __fields_order__: List[str] = []
    __field_serializers__: Dict[str, tuple] = {}
    __field_before_validators__: Dict[str, List[Any]] = {}
    __model_serializer_fn__ = None

    def __init__(self, **data):
        cls = type(self)
        for fname in cls.__fields_order__:
            if fname in data:
                value = data[fname]
            else:
                info = cls.__model_fields__.get(fname, FieldInfo())
                if info.default is not _UNDEFINED:
                    value = info.default
                elif info.default_factory is not None:
                    value = info.default_factory()
                else:
                    value = None
            for validator in cls.__field_before_validators__.get(fname, []):
                value = validator(cls, value)
            setattr(self, fname, value)

    # ---- 序列化 ----
    def _dump_fields(self, exclude_none=False, mode="python"):
        cls = type(self)
        out = {}
        for fname in cls.__fields_order__:
            value = getattr(self, fname, None)
            ser = cls.__field_serializers__.get(fname)
            if ser is not None:
                fn, when_used = ser
                if when_used == "unless-none" and value is None:
                    pass
                else:
                    value = fn(self, value)
            if exclude_none and value is None:
                continue
            out[fname] = _plain(value, exclude_none, mode)
        return out

    def model_dump(self, *, exclude_none=False, mode="python", exclude=None,
                   include=None, by_alias=False, **kwargs):
        cls = type(self)
        data = self._dump_fields(exclude_none=exclude_none, mode=mode)
        if exclude:
            data = {k: v for k, v in data.items() if k not in exclude}
        if include:
            data = {k: v for k, v in data.items() if k in include}
        model_ser = cls.__model_serializer_fn__
        if model_ser is not None:
            def handler(_inst):
                return _inst._dump_fields(exclude_none=exclude_none, mode=mode)
            data = model_ser(self, handler)
        return data

    def model_dump_json(self, **kwargs):
        import json
        return json.dumps(self.model_dump(**kwargs), ensure_ascii=False)

    # 旧接口别名
    dict = model_dump
    json = model_dump_json

    @classmethod
    def model_validate(cls, obj, **kwargs):
        if isinstance(obj, cls):
            return obj
        if isinstance(obj, dict):
            return cls(**obj)
        return obj

    @classmethod
    def parse_obj(cls, obj):
        return cls.model_validate(obj)

    @classmethod
    def model_rebuild(cls, *args, **kwargs):
        """真实 pydantic 用于解析前向引用；兜底层无需处理类型，直接返回成功。"""
        return True
