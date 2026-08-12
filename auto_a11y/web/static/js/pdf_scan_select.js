/*
 * File-select screen behaviour, transcribed from pdfMax's
 * FileSelectScreen.tsx (drag-over state, .pdf filtering on drop) and
 * AuditingScreen (the busy state shown while the audit runs).
 *
 * pdfMax handed the dropped file to Electron's main process by path. A
 * browser has no path, so the dropped File is assigned to the real
 * <input type="file"> via DataTransfer — one control behind both the
 * drop zone and the button, which also keeps the form submission
 * identical either way.
 */
(function () {
    'use strict';

    const form = document.getElementById('pdf-scan-form');
    const input = document.getElementById('pdf-scan-file');
    const dropzone = document.getElementById('pdf-scan-dropzone');
    const auditing = document.getElementById('pdf-scan-auditing');
    const auditingName = document.getElementById('pdf-scan-auditing-filename');

    if (!form || !input || !dropzone) {
        return;
    }

    /* Choosing a file starts the scan — there is no second confirmation
     * step in pdfMax and none here. */
    input.addEventListener('change', function () {
        if (input.files && input.files.length > 0) {
            form.requestSubmit();
        }
    });

    form.addEventListener('submit', function () {
        const file = input.files && input.files[0];
        if (auditingName && file) {
            auditingName.textContent = file.name;
        }
        /* Swap the form for the busy state. The live region is inside
         * `auditing`, so it has to become visible before it can announce;
         * unhiding it first, then hiding the form, keeps that order. */
        if (auditing) {
            auditing.hidden = false;
        }
        form.hidden = true;
    });

    dropzone.addEventListener('dragover', function (event) {
        event.preventDefault();
        dropzone.classList.add('drag-over');
    });

    dropzone.addEventListener('dragleave', function () {
        dropzone.classList.remove('drag-over');
    });

    dropzone.addEventListener('drop', function (event) {
        event.preventDefault();
        dropzone.classList.remove('drag-over');

        const dropped = Array.prototype.slice.call(event.dataTransfer.files)
            .filter(function (f) {
                return f.name.toLowerCase().endsWith('.pdf');
            });
        if (dropped.length === 0) {
            return;
        }

        const transfer = new DataTransfer();
        transfer.items.add(dropped[0]);
        input.files = transfer.files;
        /* Assigning `.files` does not fire `change`, so ask explicitly. */
        form.requestSubmit();
    });
}());
