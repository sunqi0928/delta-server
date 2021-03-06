from dal import delete_data
from dal import load_data
from dal import store_data
from dal.db import db
import cPickle as pickle
import dateutil.parser
from utils.exception import ModelParentKeyMissingValue



class Attr(object):

    def __init__(self, type, default=None):
        self.name = None
        self.type = type
        self.default = default

    def __get__(self, instance, owner):
        return instance._data[self.name]

    def __set__(self, instance, value):
        if instance.is_oid_key(self.name):
            instance.record_old_oid()
        elif instance.is_index_attr(self.name):
            # remove data update
            old_val = instance._data.get(self.name)
            old_orig = instance._origin_data.get(self.name)
            if old_orig is None and old_val is not None:
                instance._origin_data[self.name] = old_val
        instance._data[self.name] = value

    def _load(self, instance, value):
        raise NotImplementedError

    def _store(self, instance, value):
        raise NotImplementedError

    def _delete(self, instance, value):
        return


class _DataAttr(Attr):

    def _load(self, instance, value):
        return value and self.type(value)

    def _store(self, instance, value):
        return value


class TextAttr(_DataAttr):

    def __init__(self, default=None):
        super(TextAttr, self).__init__(str, default)


class IntAttr(_DataAttr):

    def __init__(self, default=None):
        super(IntAttr, self).__init__(int, default)


class LongAttr(_DataAttr):

    def __init__(self, default=None):
        super(LongAttr, self).__init__(long, default)


class FloatAttr(_DataAttr):

    def __init__(self, default=None):
        super(FloatAttr, self).__init__(float, default)


class BoolAttr(_DataAttr):

    def __init__(self, default=None):
        super(BoolAttr, self).__init__(bool, default)


class DateTimeAttr(_DataAttr):

    def __init__(self, default=None):
        super(DateTimeAttr, self).__init__(str, default)

    def _load(self, instance, value):
        return value and dateutil.parser.parse(value)

    def _store(self, instance, value):
        return value and value.isoformat()

class PickleAttr(_DataAttr):

    def __init__(self, default=None):
        super(PickleAttr, self).__init__(str, default)

    def _load(self, instance, value):
        return value and pickle.loads(value.decode('string-escape'))

    def _store(self, instance, value):
        return value and pickle.dumps(value).encode('string-escape')

class ListAttr(Attr):

    def _load(self, instance, value):
        if value:
            return [self.type._load(None, v) for v in value]
        else:
            return []

    def _store(self, instance, value):
        assert isinstance(value, list) or value is None
        return [self.type._store(None, v) for v in value]

#TODO: discuss on if we need this or not --Hugo
#class RefAttr(Attr):
#
#    def _load(self, instance, value):
#        self.type.owner_oid = instance.oid
#        return self.type.load(value)
#
#    def _store(self, instance, value):
#        assert isinstance(value, self.type) or value is None
#        obj = instance._data[self.name]
#        if obj is not None:
#            obj.owner_oid = instance.oid
#            self.type.owner_oid = instance.oid
#            obj.store()
#        return obj.oid
#
#    def _delete(self, instance, value):
#        assert isinstance(value, self.type) or value is None
#        if value:
#            value.delete()
#
#
#class RefListAttr(Attr):
#
#    def _load(self, instance, value):
#        resp = None
#        if value:
#            resp = []
#            for v in value:
#                self.type.owner_oid = instance.oid
#                resp.append(self.type.load(v))
#        return resp
#
#    def _store(self, instance, value):
#        assert isinstance(value, list) or value is None
#        oids = []
#        if value:
#            for obj in value:
#                assert isinstance(obj, self.type)
#                self.type.owner_oid = instance.oid
#                obj.store()
#                oids.append(obj.oid)
#            return oids
#        else:
#            return None
#
#    def _delete(self, instance, value):
#        assert isinstance(value, list) or value is None
#        for v in value or []:
#            assert isinstance(v, self.type)
#            self.type.owner_oid = instance.oid
#            v.delete()

class DictAttr(Attr):

    def _load(self, instance, value):
        if value:
            return {k: attr._load(None, value[k]) for
                    k, attr in self.type.iteritems()}
        else:
            return None

    def _store(self, instance, value):
        assert isinstance(value, dict) or value is None
        return value


basic_attribute_type = [TextAttr, IntAttr, LongAttr, FloatAttr, BoolAttr]
index_attribute_type = basic_attribute_type + [BoolAttr]

class BaseMeta(type):

    def __new__(cls, name, bases, attrs):
        if name != "Base":
            assert attrs["_oid_key"], "Please specify oid key."
            _oid_type = type(attrs.get(attrs["_oid_key"]))
            assert _oid_type in basic_attribute_type, (
                "Invalid oid key, only attribute whose type in TextAttr, "
                "IntAttr, LongAttr, FloadAttr can be a oid key.")

        # Verity index attribute
        # 1. _index_attributes must be a str list.
        # 2. Only support Text, Int, Long, Float, Bool to be index attribute.
        # 3. If model specified parent key index attribute can only be
        #    the parent key.
        p_key = attrs.get("_parent_key")
        index_attrs = attrs.get("_index_attributes", [])
        invalid_type = ("Invalid index attribute(%s), only whose type in "
                        "TextAttr, IntAttr, LongAttr, FloatAttr, BoolAttr "
                        "can be index attribute.")
        pkey_msg = ("Invalid index attribute, only %s "
                    "can be index attribute." % p_key)
        assert isinstance(index_attrs, list), "_index_attributes must be list."
        for _attr in index_attrs:
            assert isinstance(_attr, str), "Attribute name must be str."
            if p_key:
                assert _attr == p_key, pkey_msg
            _type = type(attrs.get(_attr))
            assert _type in index_attribute_type, invalid_type % _attr

        data_attrs = {}
        for attr_name, attr_val in attrs.iteritems():
            if isinstance(attr_val, Attr):
                attr_val.name = attr_name
                data_attrs[attr_name] = attr_val


        attrs['_data_attrs'] = data_attrs
        return type.__new__(cls, name, bases, attrs)


class Base(object):
    __metaclass__ = BaseMeta
    #_owner_class = None
    #_owner_oid = None
    _index_attributes = []
    _oid_key = None
    _parent_key = None

    def __init__(self, **kw):
        self._data = {}
        self._origin_data = {}
        self._origin_oid = None
        self.set_attr(**kw)

    def set_attr(self, **kw):
        for attr_name in self._data_attrs.iterkeys():
            setattr(self, attr_name, kw.get(attr_name))

    @property
    def oid(self):
        return getattr(self, self._oid_key)

    @oid.setter
    def oid(self, val):
        setattr(self, self._oid_key, val)

    @property
    def parent_key(self):
        if self._parent_key is None:
            return None
        val = getattr(self, self._parent_key)
        if val is None:
            raise ModelParentKeyMissingValue(self._parent_key)
        return val

    @parent_key.setter
    def parent_key(self, val):
        setattr(self, self._parent_key, val)

    def is_index_attr(self, attr_name):
        return attr_name in self._index_attributes

    def is_oid_key(self, attr_val):
        return attr_val == self._oid_key

    def record_old_oid(self):
        old_oid = self._data.get(self._oid_key)
        if self._origin_oid is None and old_oid:
            self._origin_oid = old_oid

    #@property
    #def owner_oid(self):
    #    return getattr(self, '_owner_oid')

    #@owner_oid.setter
    #def owner_oid(self, val):
    #    setattr(self, '_owner_oid', val)

    #@classmethod
    #def _get_key(cls, oid):
    #    owner_class = cls._owner_class
    #    owner_oid = cls.owner_oid
    #    if owner_class:
    #        assert owner_oid is not None
    #        key = '%s:%s:%s' % (owner_class._get_key(owner_oid), cls.__name__, oid)
    #    else:
    #        key = '%s:%s' % (cls.__name__, oid)
    #    return key


    @classmethod
    def _get_index_key(self, attribute_name, val):
        return "%s:%s:%s" % (self.__name__, attribute_name, val)

    def _get_key(self):
        key = "%s:%s" % (self.__class__.__name__, self.oid)
        parent_key = self.parent_key
        if parent_key is not None:
            key += ':%s:%s' % (self._parent_key, parent_key)
        return key

    def _get_data(self):
        key = self._get_key()
        return load_data(key)

    def exist(self):
        return self._get_data() is not None

    def load(self):
        data = self._get_data()
        if data is not None:
            self.set_attr(**data)
            oid_key = type(self)._oid_key
            self.oid = data.get(oid_key)
            for attr_name, field in self._data_attrs.iteritems():
                self._data[attr_name] = field._load(self, data.get(attr_name))
        return self

    def store(self):
        cls = type(self)
        if self._origin_oid is not None and self._origin_oid != self.oid:
            # delete old data, remove from attr index list
            kwargs = {self._oid_key: self._origin_oid}
            if self._parent_key:
                kwargs[self._parent_key] = self.parent_key
            for attr_name in self._index_attributes:
                kwargs[attr_name] = self._origin_data.get(
                    attr_name, getattr(self, attr_name))
            self._origin_data.clear()
            old_inst = cls(**kwargs)
            old_inst.delete()
        elif self._origin_data:
            # remove from old attr index list if index attr value changes.
            for attr_name, old_val in self._origin_data.iteritems():
                if old_val != getattr(self, attr_name):
                    self.delete_from_index_attribute(attr_name, val=old_val)
            self._origin_data.clear()

        json_data = {}
        for attr_name, field in self._data_attrs.iteritems():
            if self._data[attr_name] is None:
                continue
            json_data[attr_name] = field._store(self, self._data[attr_name])
        key = self._get_key()
        store_data(key, json_data)

        # save for query by attribute & value
        for attr_name in self._index_attributes:
            val = getattr(self, attr_name)
            if val is None:
                continue
            origin_val = cls.load_oids_by_attribute(attr_name, val)
            if self.oid in origin_val:
                continue
            origin_val.append(self.oid)
            index_key = cls._get_index_key(attr_name, val)
            store_data(index_key, origin_val)

    @classmethod
    def load_oids_by_attribute(cls, attribute, val):
        key = cls._get_index_key(attribute, val)
        oid_list = load_data(key)
        return oid_list or []

    @classmethod
    def load_by_attribute(cls, attribute, val):
        # get oid by attribute val, then load data
        if attribute not in cls._index_attributes:
            return None
        rep = []
        for oid in cls.load_oids_by_attribute(attribute, val):
            data = {attribute: val,
                    cls._oid_key: oid}
            obj = cls(**data)
            obj.load()
            rep.append(obj)
        return rep

    def delete_from_index_attribute(self, attr_name, val=None):
        cls = type(self)
        val = val or getattr(self, attr_name)
        origin_val = cls.load_oids_by_attribute(attr_name, val)
        if self.oid in origin_val:
            origin_val.remove(self.oid)
            index_key = cls._get_index_key(attr_name, val)
            if origin_val:
                store_data(index_key, origin_val)
            else:
                delete_data(index_key)

    def delete(self):

        # delete refs Data
        #for attr_name, field in self._data_attrs.iteritems():
        #    if type(field) in (RefAttr, RefListAttr):
        #        field._delete(self, self._data[attr_name])

        # delete from index attribute
        for attr_name in self._index_attributes:
            self.delete_from_index_attribute(attr_name)

        # delete from cache & db
        key = self._get_key()
        delete_data(key)


class KeyValue(object):

    def __init__(self, key):
        self.key = key

    def load(self):
        return db.load(self.key)

    def store(self, value):
        return db.set(self.key, value)

    def incr(self, amount=1, initial=0):
        return db.incr(self.key, amount, initial).value
