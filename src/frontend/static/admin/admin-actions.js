(function () {
    'use strict';
    document.querySelectorAll('form[data-confirm]').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (!window.confirm(form.dataset.confirm)) event.preventDefault();
        });
    });
    var items = document.getElementById('order-items');
    var add = document.getElementById('add-order-item');
    if (!items || !add) return;
    add.addEventListener('click', function () {
        if (items.children.length >= 20) return;
        var row = items.firstElementChild.cloneNode(true);
        row.querySelector('select').value = '';
        row.querySelector('input').value = '1';
        items.appendChild(row);
    });
    items.addEventListener('click', function (event) {
        if (!event.target.classList.contains('remove-order-item')) return;
        var row = event.target.closest('.order-item-row');
        if (items.children.length > 1) row.remove();
        else { row.querySelector('select').value = ''; row.querySelector('input').value = '1'; }
    });
})();
