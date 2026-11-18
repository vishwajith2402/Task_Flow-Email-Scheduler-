import tkinter.ttk as ttk

import customtkinter as ctk


def setup_theme(palette: dict[str, str]) -> None:
    ctk.set_appearance_mode(palette["appearance_mode"])
    ctk.set_default_color_theme("blue")


def style_treeview(palette: dict[str, str]) -> None:
    style = ttk.Style()
    style.theme_use("default")
    style.configure(
        "Treeview",
        background=palette["card"],
        foreground=palette["text"],
        fieldbackground=palette["card"],
        bordercolor=palette["border"],
        rowheight=32,
    )
    style.map("Treeview", background=[("selected", palette["accent"])], foreground=[("selected", palette["bg"])])
    style.configure(
        "Treeview.Heading",
        background=palette["section"],
        foreground=palette["text"],
        relief="flat",
        bordercolor=palette["border"],
        padding=8,
    )
