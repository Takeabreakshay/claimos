"""ClaimOS live layer — real, working implementations of the components that do
NOT require regulator-gated credentials.

  vision.py    photo quality / blur / perceptual-hash reuse / EXIF   (no key)
  ocr.py       document OCR + field extraction                       (no key, better w/ LLM key)
  store.py     Supabase persistence + storage + audit trail          (Supabase key)
  workflow.py  the live claim state machine that ties it together

The four regulator-gated rails (VAHAN, DigiLocker, IIB PRISM/QUEST) remain in
``src/rails.py`` behind ``# PRODUCTION:`` swap points — see .env.example.
"""
