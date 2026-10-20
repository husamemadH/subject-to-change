import multiprocessing

from subject_to_change.launch import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
