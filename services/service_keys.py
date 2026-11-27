"""Keys for the services you bring (dictation, embeddings, speech), kept
only in the desktop keyring (Secret Service), one per purpose."""

from contextlib import closing


def _attributes(purpose):
    return {"application": "steed", "purpose": purpose}


def read(purpose):
    """The kept key, or "" when there is none or the keyring is unavailable."""
    try:
        import secretstorage

        with closing(secretstorage.dbus_init()) as connection:
            collection = secretstorage.get_default_collection(connection)
            if collection.is_locked():
                collection.unlock()
            items = list(collection.search_items(_attributes(purpose)))
            if not items:
                return ""
            if items[0].is_locked():
                items[0].unlock()
            return items[0].get_secret().decode()
    except Exception:
        return ""


def write(purpose, label, value):
    """Keep value as the key (or with "" forget it); raises if the keyring
    cannot be reached."""
    import secretstorage

    with closing(secretstorage.dbus_init()) as connection:
        collection = secretstorage.get_default_collection(connection)
        if collection.is_locked():
            collection.unlock()
        for item in collection.search_items(_attributes(purpose)):
            item.delete()
        if value:
            collection.create_item(
                label, _attributes(purpose), value.encode(), replace=True
            )
