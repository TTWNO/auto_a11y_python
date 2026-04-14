document.addEventListener('DOMContentLoaded', function() {
    // Wire data-open-modal="<selector>" buttons
    document.addEventListener('click', function(e) {
        var opener = e.target.closest('[data-open-modal]');
        if (opener) {
            e.preventDefault();
            var dialog = document.querySelector(opener.getAttribute('data-open-modal'));
            if (dialog) dialog.showModal();
        }
    });

    // Wire data-close-modal buttons
    document.addEventListener('click', function(e) {
        var closer = e.target.closest('[data-close-modal]');
        if (closer) {
            var dialog = closer.closest('dialog');
            if (dialog) dialog.close();
        }
    });

    // Backdrop click to close (skip persistent dialogs)
    document.addEventListener('click', function(e) {
        if (e.target.tagName === 'DIALOG' && e.target.open
            && !e.target.hasAttribute('data-modal-persistent')) {
            e.target.close();
        }
    });

    // Prevent Escape on persistent dialogs (cancel does not bubble, use capture)
    document.addEventListener('cancel', function(e) {
        if (e.target.tagName === 'DIALOG'
            && e.target.hasAttribute('data-modal-persistent')) {
            e.preventDefault();
        }
    }, true);
});

// Programmatic helpers for inline scripts
function openModal(id) {
    var dialog = document.getElementById(id);
    if (dialog) dialog.showModal();
}

function closeModal(id) {
    var dialog = document.getElementById(id);
    if (dialog) dialog.close();
}
