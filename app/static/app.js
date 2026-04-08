// Minimal JS for interactivity — no frameworks needed
document.addEventListener('DOMContentLoaded', function() {
    // Select All / Deselect All toggle for duplicate groups
    document.querySelectorAll('.duplicate-group header').forEach(function(header) {
        header.style.cursor = 'pointer';
        header.title = 'Click to toggle all checkboxes in this group';
        header.addEventListener('click', function() {
            const group = header.closest('.duplicate-group');
            const checkboxes = group.querySelectorAll('input[type="checkbox"]');
            const allChecked = Array.from(checkboxes).every(cb => cb.checked);
            checkboxes.forEach(cb => { cb.checked = !allChecked; });
            // Trigger change event to update count
            const form = document.getElementById('delete-form');
            if (form) form.dispatchEvent(new Event('change'));
        });
    });
});
