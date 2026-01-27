#!/usr/bin/env python
# -*- coding: utf-8 -*-

search = "почта"
title = "Проверить почту"

print(f"Search: '{search}'")
print(f"Title: '{title}'")
print(f"Search lower: '{search.lower()}'")
print(f"Title lower: '{title.lower()}'")
print(f"Result: {search.lower() in title.lower()}")
print(f"Search bytes: {search.encode('utf-8')}")
print(f"Title bytes: {title.encode('utf-8')}")
