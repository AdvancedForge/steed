"""Packaged recipe discovery as data; installation still requires user review."""

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from services.connector_recipes import (
    MAX_RECIPE_BYTES,
    ConnectorRecipe,
    RecipeError,
    parse_recipe,
)

MAX_BUNDLED_RECIPES = 50


@dataclass(frozen=True)
class BundledRecipe:
    slug: str
    recipe: ConnectorRecipe


class BundledRecipes:
    def __init__(self, directory=None):
        self.directory = (
            Path(directory)
            if directory is not None
            else Path(__file__).resolve().parents[1] / "connectors" / "recipes"
        )

    def read(self, slug):
        if not isinstance(slug, str) or not re.fullmatch(
            r"[a-z][a-z0-9_-]{0,63}", slug
        ):
            raise RecipeError("Choose a packaged connector")
        path = self.directory / (slug + ".json")
        with os.fdopen(
            os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb"
        ) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise RecipeError("Packaged connectors must be regular JSON files")
            raw = stream.read(MAX_RECIPE_BYTES + 1)
        return BundledRecipe(slug, parse_recipe(raw))

    def list(self):
        try:
            names = sorted(
                p.name
                for p in self.directory.iterdir()
                if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}\.json", p.name)
            )
        except OSError:
            return ()
        entries = []
        # Bound work even if a distribution ships too many or malformed entries.
        for name in names[:MAX_BUNDLED_RECIPES]:
            try:
                entries.append(self.read(name[:-5]))
            except (OSError, ValueError):
                continue
        return tuple(entries)

    def select(self, entry):
        current = self.read(entry.slug)
        if current != entry:
            raise RecipeError("Packaged connector changed; reopen its review")
        return current.recipe
