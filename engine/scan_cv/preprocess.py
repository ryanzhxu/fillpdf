"""Grayscale + denoise + binarize a rendered scan bitmap.

Deliberately excludes deskew (see engine/scan_cv/deskew.py) -- this is the
standard, low-risk part of the pipeline; deskew is the part that needed
real iteration to get right during design.
"""
import cv2


def preprocess(bitmap):
    """bitmap: a BGR image (as cv2.imread returns). Returns a single-channel
    binary image (foreground=255, background=0), same pixel dimensions."""
    gray = cv2.cvtColor(bitmap, cv2.COLOR_BGR2GRAY)
    gray = cv2.medianBlur(gray, 3)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV,
        blockSize=25, C=10)
    return binary
