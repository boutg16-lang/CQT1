# ci-fix — ملفات الـ workflow النهائية (Issue #25 + #26)

هذه نسخ **جاهزة ومُختبَرة** من ملفَّي الـ workflow المُصلَحين. لا تُشغَّل مباشرةً
من هنا؛ تُنسخ إلى `.github/workflows/` بواسطة:

```bash
python -m scripts.apply_ci_workflow_fix --apply --push
```

- `build-exe.yml` — `EXE_NAME` كمصدر واحد، خطوة تحقق من تطابق الـ spec، smoke test
  محصَّن، وتوليد `checksums.txt` (يُرفق بالـ Release ليعمل `auto_updater`)، وإنشاء
  الـ Release قبل رفع الـ artifact.
- `ci.yml` — `pytest` قبل رفع الـ artifact و`retention-days: 7` (حصة التخزين).

مصدر التغيير التفصيلي: `docs/ISSUE_25_workflow_fix.patch` و`docs/ISSUE_25_WORKFLOW_FIX_AR.md`.
