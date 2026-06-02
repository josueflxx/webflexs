/**
 * FLEXS B2B - Main JavaScript
 */

document.addEventListener('DOMContentLoaded', function () {
    const PUBLIC_NAV_MOBILE_BREAKPOINT = 900;
    // Mobile menu toggle
    const mobileMenuToggle = document.getElementById('mobileMenuToggle');
    const navMenu = document.getElementById('navMenu');
    const header = document.querySelector('.header');
    const root = document.documentElement;
    const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

    function updateHeaderOffset() {
        if (!root || !header) return;
        const height = header.offsetHeight || 0;
        const offset = Math.max(height, 64);
        root.style.setProperty('--header-offset', `${offset}px`);
    }

    updateHeaderOffset();
    window.addEventListener('resize', updateHeaderOffset);
    window.addEventListener('orientationchange', updateHeaderOffset);
    window.addEventListener('load', updateHeaderOffset);

    if (mobileMenuToggle && navMenu) {
        function setMenuState(isOpen) {
            navMenu.classList.toggle('active', isOpen);
            mobileMenuToggle.classList.toggle('active', isOpen);
            mobileMenuToggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
            mobileMenuToggle.setAttribute('aria-label', isOpen ? 'Cerrar menu' : 'Abrir menu');
            const isMobile = window.innerWidth <= PUBLIC_NAV_MOBILE_BREAKPOINT;
            navMenu.setAttribute('aria-hidden', isMobile && !isOpen ? 'true' : 'false');
        }

        setMenuState(navMenu.classList.contains('active'));

        mobileMenuToggle.addEventListener('click', function () {
            setMenuState(!navMenu.classList.contains('active'));
        });

        // Close menu when clicking a link
        navMenu.querySelectorAll('a').forEach(link => {
            link.addEventListener('click', () => {
                setMenuState(false);
            });
        });

        // Keep layout consistent when resizing between mobile/desktop breakpoints.
        window.addEventListener('resize', () => {
            if (window.innerWidth > PUBLIC_NAV_MOBILE_BREAKPOINT) {
                setMenuState(false);
            }
        });
    }

    // Smooth scroll for anchor links
    document.querySelectorAll('a[href*="#"]').forEach(anchor => {
        anchor.addEventListener('click', function (e) {
            const href = this.getAttribute('href');
            if (!href || href === '#') return;

            let url;
            try {
                url = new URL(href, window.location.href);
            } catch (err) {
                return;
            }

            if (url.origin !== window.location.origin || url.pathname !== window.location.pathname) {
                return;
            }

            const targetId = url.hash;
            if (!targetId) return;

            const target = document.querySelector(targetId);
            if (target) {
                e.preventDefault();
                const cssOffset = parseFloat(
                    getComputedStyle(document.documentElement).getPropertyValue('--header-offset')
                );
                const headerOffset = Number.isFinite(cssOffset)
                    ? cssOffset
                    : (header ? header.offsetHeight : 0);
                const elementPosition = target.getBoundingClientRect().top;
                const offsetPosition = elementPosition + window.pageYOffset - headerOffset;

                window.scrollTo({
                    top: offsetPosition,
                    behavior: prefersReducedMotion.matches ? 'auto' : 'smooth'
                });
                history.pushState(null, '', targetId);
            }
        });
    });

    // Auto-dismiss alerts after 5 seconds
    document.querySelectorAll('.alert').forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.3s ease';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 300);
        }, 5000);
    });

    // Header scroll effect
    if (header) {
        window.addEventListener('scroll', () => {
            if (window.scrollY > 50) {
                header.classList.add('scrolled');
            } else {
                header.classList.remove('scrolled');
            }
        });
    }

    // Admin UX: allow horizontal scrolling overflow panels/tables using right-click drag.
    if (document.body.classList.contains('admin-body')) {
        function resetAdminPageHorizontalScroll() {
            if (window.scrollX) {
                window.scrollTo(0, window.scrollY || 0);
            }
            if (document.documentElement) {
                document.documentElement.scrollLeft = 0;
                document.documentElement.style.overflowX = 'hidden';
            }
            if (document.body) {
                document.body.scrollLeft = 0;
                document.body.style.overflowX = 'hidden';
            }
        }

        resetAdminPageHorizontalScroll();
        window.addEventListener('load', resetAdminPageHorizontalScroll);
        window.addEventListener('resize', resetAdminPageHorizontalScroll);

        const adminDragScrollSelector = [
            '.admin-top-nav',
            '.toolbar-actions',
            '.form-actions',
            '.cc-quick-actions',
            '.cc-links',
            '.cc-tabs',
            '.products-table-wrapper',
            '.category-table-wrap',
            '.execution-table-wrap',
            '.admin-table-container',
            '.admin-detail-table-wrap',
            '.report-standalone-table-wrap',
            '.sales-record-actions',
            '[data-drag-scroll]'
        ].join(',');

        const adminWheelScrollSelector = [
            '.admin-top-nav',
            '.header-user',
            '.execution-table-wrap',
            '.admin-detail-table-wrap',
            '.report-standalone-table-wrap',
            '.sales-record-table-wrap',
            '.doc-type-table-wrap',
            '.order-table-wrap',
            '[data-wheel-scroll]',
            '[data-drag-scroll]'
        ].join(',');

        const interactiveBlockSelector = [
            'input',
            'textarea',
            'select',
            'button',
            'a',
            'label',
            '[contenteditable="true"]',
            '.no-drag-scroll',
            '.search-suggestions',
            '.suggestion-item'
        ].join(',');

        const scrollableOverflowValues = new Set(['auto', 'scroll', 'overlay']);
        let dragState = null;
        let suppressContextMenuOnce = false;

        function isElementScrollable(container) {
            if (!(container instanceof HTMLElement)) return false;
            const computed = window.getComputedStyle(container);
            const canScrollX = scrollableOverflowValues.has(computed.overflowX)
                && container.scrollWidth > container.clientWidth;
            return canScrollX;
        }

        function getDragScrollContainer(startElement) {
            if (!(startElement instanceof Element)) return null;
            let current = startElement;
            while (current && current !== document.body) {
                if (current.matches(adminDragScrollSelector) && isElementScrollable(current)) {
                    return current;
                }
                current = current.parentElement;
            }
            return null;
        }

        function getWheelScrollContainer(startElement) {
            if (!(startElement instanceof Element)) return null;
            let current = startElement;
            while (current && current !== document.body) {
                if (current.matches(adminWheelScrollSelector) && isElementScrollable(current)) {
                    return current;
                }
                current = current.parentElement;
            }
            return null;
        }

        function canScrollHorizontallyInDirection(container, delta) {
            if (!container || !delta) return false;
            const maxScrollLeft = container.scrollWidth - container.clientWidth;
            if (maxScrollLeft <= 0) return false;
            if (delta > 0) return container.scrollLeft < maxScrollLeft - 1;
            return container.scrollLeft > 1;
        }

        function endRightDragScroll(event) {
            if (!dragState) return;
            const dragged = dragState.moved;
            dragState.container.classList.remove('drag-scroll-active');
            dragState = null;
            document.body.classList.remove('admin-drag-scroll-lock');

            if (dragged && event) {
                event.preventDefault();
                window.setTimeout(() => {
                    suppressContextMenuOnce = false;
                }, 0);
            } else {
                suppressContextMenuOnce = false;
            }
        }

        document.addEventListener('mousedown', function startRightDragScroll(event) {
            if (event.button !== 2) return;
            const target = event.target;
            if (!(target instanceof Element)) return;

            const explicitScrollableContainer = target.closest(
                '.toolbar-actions, .form-actions, .cc-quick-actions, .cc-links, .cc-tabs, [data-drag-scroll]'
            );
            if (target.closest(interactiveBlockSelector) && !explicitScrollableContainer) return;

            const container = getDragScrollContainer(target);
            if (!container) return;

            dragState = {
                container,
                startClientX: event.clientX,
                startScrollLeft: container.scrollLeft,
                moved: false,
            };
            container.classList.add('drag-scroll-active');
            document.body.classList.add('admin-drag-scroll-lock');
            event.preventDefault();
        }, true);

        document.addEventListener('wheel', function wheelHorizontalOverflow(event) {
            if (event.ctrlKey || event.shiftKey) return;

            const target = event.target;
            if (!(target instanceof Element)) return;
            if (target.closest('textarea, select, [contenteditable="true"], .no-wheel-scroll')) return;
            if (target.closest('.products-table-wrapper, .category-table-wrap')) {
                resetAdminPageHorizontalScroll();
                return;
            }

            const hasHorizontalWheel = Math.abs(event.deltaX) > 0;
            const dominantDelta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
                ? event.deltaX
                : event.deltaY;
            const container = getWheelScrollContainer(target);

            if (container) {
                if (dominantDelta && canScrollHorizontallyInDirection(container, dominantDelta)) {
                    container.scrollLeft += dominantDelta;
                }
                if (dominantDelta || hasHorizontalWheel) {
                    event.preventDefault();
                    resetAdminPageHorizontalScroll();
                }
                return;
            }

            if (hasHorizontalWheel) {
                event.preventDefault();
            }

            window.requestAnimationFrame(() => {
                resetAdminPageHorizontalScroll();
            });
        }, { passive: false, capture: true });

        document.addEventListener('mousemove', function moveRightDragScroll(event) {
            if (!dragState) return;

            if ((event.buttons & 2) !== 2) {
                endRightDragScroll(event);
                return;
            }

            const deltaX = event.clientX - dragState.startClientX;
            if (!dragState.moved && Math.abs(deltaX) > 2) {
                dragState.moved = true;
                suppressContextMenuOnce = true;
            }

            dragState.container.scrollLeft = dragState.startScrollLeft - deltaX;
            event.preventDefault();
        }, true);

        document.addEventListener('mouseup', endRightDragScroll, true);
        window.addEventListener('blur', function () {
            endRightDragScroll();
        });

        document.addEventListener('contextmenu', function (event) {
            if (!suppressContextMenuOnce) return;
            event.preventDefault();
            suppressContextMenuOnce = false;
        }, true);
    }

    /* ============================================
       FLEXS Premium Animations & Global JS Logic
       ============================================ */

    // 3. MutationObserver: Cart Badge Bounce
    const cartBadge = document.getElementById('cartBadge');
    if (cartBadge) {
        const observer = new MutationObserver(() => {
            cartBadge.classList.remove('bounce');
            void cartBadge.offsetWidth; // Trigger DOM reflow to restart animation
            cartBadge.classList.add('bounce');
        });
        observer.observe(cartBadge, { childList: true, characterData: true, subtree: true });
    }

    // 5. 3D Tilt Effect on cards (data-tilt)
    const tilts = document.querySelectorAll('[data-tilt]');
    tilts.forEach(el => {
        el.addEventListener('mousemove', e => {
            const rect = el.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const xc = rect.width / 2;
            const yc = rect.height / 2;
            const dx = x - xc;
            const dy = y - yc;
            const tiltX = -(dy / yc) * 8; // max tilt degrees: 8
            const tiltY = (dx / xc) * 8;
            el.style.transform = `perspective(1000px) rotateX(${tiltX}deg) rotateY(${tiltY}deg) translateY(-5px)`;
        });
        el.addEventListener('mouseleave', () => {
            el.style.transform = 'perspective(1000px) rotateX(0deg) rotateY(0deg) translateY(0)';
        });
    });

    // 6 & 14. Intersection Observer for Scroll Reveals (sections, cascade lists)
    const observerOptions = {
        root: null,
        rootMargin: '0px 0px 50px 0px',
        threshold: 0.01
    };
    const revealObserver = new IntersectionObserver((entries, observer) => {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('revealed');
                observer.unobserve(entry.target);
            }
        });
    }, observerOptions);

    document.querySelectorAll('.reveal-on-scroll, .related-products-grid').forEach(el => {
        revealObserver.observe(el);
    });

    // 7. Interactive Flashlight Glow Border tracker for category cards
    document.querySelectorAll('.product-category-card').forEach(card => {
        card.addEventListener('mousemove', e => {
            const rect = card.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            card.style.setProperty('--mouse-x', `${x}px`);
            card.style.setProperty('--mouse-y', `${y}px`);
        });
    });

    // 13. Interactive high-precision zoom magnifier (lupa) for product detail page
    const mediaContainers = document.querySelectorAll('.product-media');
    mediaContainers.forEach(container => {
        const img = container.querySelector('img');
        if (!img) return;

        container.style.position = 'relative';
        container.style.overflow = 'hidden';

        container.addEventListener('mousemove', e => {
            const rect = container.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const y = e.clientY - rect.top;
            const xp = (x / rect.width) * 100;
            const yp = (y / rect.height) * 100;
            img.style.transformOrigin = `${xp}% ${yp}%`;
            img.style.transform = 'scale(1.8)';
        });

        container.addEventListener('mouseleave', () => {
            img.style.transform = 'scale(1)';
            img.style.transformOrigin = 'center center';
        });
    });

    /* ========================================================
       FLEXS Premium - Theme, Search, Toasts and Portal UX
       ======================================================== */

    // 1. Dual Core Theme Toggle (Modo Claro / Modo Oscuro)
    const themeToggleBtn = document.getElementById('themeToggle');
    const themeIcon = document.getElementById('themeIcon');

    // SVGs for Sun and Moon
    const sunIconPath = `<path d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path>`;
    const moonIconPath = `<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>`;

    function getPreferredTheme() {
        return localStorage.getItem('theme') || 'dark';
    }

    function initTheme() {
        const currentTheme = getPreferredTheme();
        document.documentElement.setAttribute('data-theme', currentTheme);
        if (themeIcon) {
            themeIcon.setAttribute('fill', currentTheme === 'dark' ? 'currentColor' : 'none');
            themeIcon.setAttribute('stroke', 'currentColor');
            themeIcon.setAttribute('stroke-width', currentTheme === 'dark' ? '0' : '2');
            themeIcon.innerHTML = currentTheme === 'dark' ? sunIconPath : moonIconPath;
        }
    }

    if (themeToggleBtn && themeIcon) {
        themeToggleBtn.addEventListener('click', function () {
            const activeTheme = document.documentElement.getAttribute('data-theme') || 'dark';
            const newTheme = activeTheme === 'dark' ? 'light' : 'dark';
            
            document.documentElement.setAttribute('data-theme', newTheme);
            localStorage.setItem('theme', newTheme);

            // Animate transition of the icon
            themeIcon.style.transform = 'scale(0) rotate(-90deg)';
            setTimeout(() => {
                themeIcon.setAttribute('fill', newTheme === 'dark' ? 'currentColor' : 'none');
                themeIcon.setAttribute('stroke', 'currentColor');
                themeIcon.setAttribute('stroke-width', newTheme === 'dark' ? '0' : '2');
                themeIcon.innerHTML = newTheme === 'dark' ? sunIconPath : moonIconPath;
                themeIcon.style.transform = 'scale(1) rotate(0deg)';
            }, 180);
        });
    }

    initTheme();

    // 2. Toast Notification Center API
    const toastContainer = document.getElementById('toastContainer');

    window.showFLEXSToast = function (title, message, type = 'success', duration = 5000) {
        if (!toastContainer) return;

        const toast = document.createElement('div');
        toast.className = `flexs-toast toast-${type}`;
        
        toast.innerHTML = `
            <div class="toast-header">
                <span class="toast-title">${title}</span>
                <button type="button" class="toast-close">&times;</button>
            </div>
            <div class="toast-body">${message}</div>
            <div class="toast-progress"></div>
        `;

        toastContainer.appendChild(toast);

        const closeBtn = toast.querySelector('.toast-close');
        const progressBar = toast.querySelector('.toast-progress');

        // Setup progressive countdown
        let timeoutId;
        progressBar.style.width = '100%';
        
        // Trigger transit width animation in next frame
        requestAnimationFrame(() => {
            progressBar.style.transition = `width ${duration}ms linear`;
            progressBar.style.width = '0%';
        });

        function dismissToast() {
            clearTimeout(timeoutId);
            toast.classList.add('toast-exit');
            setTimeout(() => {
                toast.remove();
            }, 350);
        }

        closeBtn.addEventListener('click', dismissToast);

        timeoutId = setTimeout(dismissToast, duration);

        // Enable swipe to dismiss on touch devices
        let startX = 0;
        toast.addEventListener('touchstart', (e) => {
            startX = e.touches[0].clientX;
        }, { passive: true });

        toast.addEventListener('touchend', (e) => {
            const diffX = e.changedTouches[0].clientX - startX;
            if (diffX > 80) { // Swiped right
                dismissToast();
            }
        }, { passive: true });
    };

    // 3. Omni-Search Overlay (Ctrl + B)
    const omniOverlay = document.getElementById('omniSearchOverlay');
    const omniInput = document.getElementById('omniSearchInput');
    const omniResults = document.getElementById('omniSearchResults');
    let searchDebounceTimeout;
    let selectedItemIndex = -1;

    function openOmniSearch() {
        if (!omniOverlay) return;
        omniOverlay.classList.add('active');
        if (omniInput) {
            omniInput.value = '';
            setTimeout(() => omniInput.focus(), 150);
        }
        if (omniResults) {
            omniResults.innerHTML = '';
            omniResults.classList.remove('has-content');
        }
        selectedItemIndex = -1;
    }

    function closeOmniSearch() {
        if (!omniOverlay) return;
        omniOverlay.classList.remove('active');
    }

    // Keydown listener for Ctrl+B globally
    window.addEventListener('keydown', function (e) {
        if ((e.ctrlKey || e.metaKey) && (e.key === 'b' || e.key === 'B')) {
            e.preventDefault();
            openOmniSearch();
        }
    });

    if (omniOverlay) {
        // Close on escape key
        window.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && omniOverlay.classList.contains('active')) {
                closeOmniSearch();
            }
        });

        // Close on clicking outside search box
        omniOverlay.addEventListener('click', function (e) {
            if (e.target === omniOverlay) {
                closeOmniSearch();
            }
        });
    }

    if (omniInput && omniResults) {
        omniInput.addEventListener('input', function () {
            clearTimeout(searchDebounceTimeout);
            const query = omniInput.value.trim();

            if (query.length < 2) {
                omniResults.innerHTML = '';
                omniResults.classList.remove('has-content');
                selectedItemIndex = -1;
                return;
            }

            searchDebounceTimeout = setTimeout(async () => {
                try {
                    const response = await fetch(`/api/search-suggestions/?q=${encodeURIComponent(query)}&scope=catalog`);
                    if (response.ok) {
                        const data = await response.json();
                        renderOmniSearchResults(data.suggestions || []);
                    }
                } catch (e) {
                    console.error('Error fetching search suggestions:', e);
                }
            }, 180);
        });

        // Results rendering
        function renderOmniSearchResults(suggestions) {
            if (!suggestions || suggestions.length === 0) {
                omniResults.innerHTML = `<div class="omni-search-empty">No se encontraron artículos ni categorías para "${omniInput.value}"</div>`;
                omniResults.classList.add('has-content');
                selectedItemIndex = -1;
                return;
            }

            let htmlContent = '';
            suggestions.forEach((item, index) => {
                let link = '';
                let iconSymbol = '⚙️';
                
                if (item.kind === 'product') {
                    link = `/catalogo/producto/${encodeURIComponent(item.value)}/`;
                    iconSymbol = '🔧';
                } else if (item.kind === 'category') {
                    const slug = item.value.replace('cat:', '');
                    link = `/catalogo/?category=${encodeURIComponent(slug)}`;
                    iconSymbol = '📂';
                }

                htmlContent += `
                    <a href="${link}" class="omni-search-item" data-index="${index}">
                        <div class="omni-search-item-img" style="display:flex;align-items:center;justify-content:center;font-size:20px;">
                            ${iconSymbol}
                        </div>
                        <div class="omni-search-item-info">
                            <span class="omni-search-item-title">${item.label}</span>
                            <span class="omni-search-item-sub">${item.meta}</span>
                        </div>
                        ${item.kind === 'product' ? `<span class="omni-search-item-price">Ver Detalle ➜</span>` : ''}
                    </a>
                `;
            });

            omniResults.innerHTML = htmlContent;
            omniResults.classList.add('has-content');
            selectedItemIndex = -1;

            // Add click listeners to items to close overlay
            omniResults.querySelectorAll('.omni-search-item').forEach(item => {
                item.addEventListener('click', () => {
                    closeOmniSearch();
                });
            });
        }

        // Arrow keys navigation in results
        omniInput.addEventListener('keydown', function (e) {
            const items = omniResults.querySelectorAll('.omni-search-item');
            if (items.length === 0) return;

            if (e.key === 'ArrowDown') {
                e.preventDefault();
                selectedItemIndex = (selectedItemIndex + 1) % items.length;
                updateSelectionState(items);
            } else if (e.key === 'ArrowUp') {
                e.preventDefault();
                selectedItemIndex = (selectedItemIndex - 1 + items.length) % items.length;
                updateSelectionState(items);
            } else if (e.key === 'Enter') {
                if (selectedItemIndex >= 0 && selectedItemIndex < items.length) {
                    e.preventDefault();
                    items[selectedItemIndex].click();
                    window.location.href = items[selectedItemIndex].getAttribute('href');
                }
            }
        });

        function updateSelectionState(items) {
            items.forEach((item, idx) => {
                if (idx === selectedItemIndex) {
                    item.classList.add('selected');
                    item.scrollIntoView({ block: 'nearest' });
                } else {
                    item.classList.remove('selected');
                }
            });
        }
    }

    // 4. Analytics Interactive SVG Tooltips
    const chartNodes = document.querySelectorAll('.chart-node');
    const segmentNodes = document.querySelectorAll('.donut-segment');
    
    // Create central tooltip DOM element if it doesn't exist
    let activeTooltip = document.querySelector('.svg-tooltip');
    if (!activeTooltip) {
        activeTooltip = document.createElement('div');
        activeTooltip.className = 'svg-tooltip';
        document.body.appendChild(activeTooltip);
    }

    chartNodes.forEach(node => {
        node.addEventListener('mouseenter', function (e) {
            const value = node.getAttribute('data-value') || '';
            const label = node.getAttribute('data-label') || '';
            activeTooltip.innerHTML = `<div><strong>${label}</strong></div><div style="color:var(--color-primary);margin-top:2px;">${value}</div>`;
            activeTooltip.classList.add('active');
        });

        node.addEventListener('mousemove', function (e) {
            activeTooltip.style.left = `${e.pageX}px`;
            activeTooltip.style.top = `${e.pageY}px`;
        });

        node.addEventListener('mouseleave', function () {
            activeTooltip.classList.remove('active');
        });
    });

    segmentNodes.forEach(segment => {
        segment.addEventListener('mouseenter', function (e) {
            const label = segment.getAttribute('data-label') || '';
            const percentage = segment.getAttribute('data-percentage') || '';
            activeTooltip.innerHTML = `<div><strong>${label}</strong></div><div style="color:var(--color-primary);margin-top:2px;">${percentage}</div>`;
            activeTooltip.classList.add('active');
        });

        segment.addEventListener('mousemove', function (e) {
            activeTooltip.style.left = `${e.pageX}px`;
            activeTooltip.style.top = `${e.pageY}px`;
        });

        segment.addEventListener('mouseleave', function () {
            activeTooltip.classList.remove('active');
        });
    });

    // 5. Interactive Tab Switching on Analytics Section
    const analyticsTabs = document.querySelectorAll('.analytics-tab');
    const chartPanes = document.querySelectorAll('.analytics-chart-pane');

    analyticsTabs.forEach(tab => {
        tab.addEventListener('click', function () {
            const targetPaneId = tab.getAttribute('data-target');
            if (!targetPaneId) return;

            // Remove active class from all tabs & panes
            analyticsTabs.forEach(t => t.classList.remove('active'));
            chartPanes.forEach(pane => pane.classList.remove('active'));

            // Add active to current
            tab.classList.add('active');
            const activePane = document.getElementById(targetPaneId);
            if (activePane) {
                activePane.classList.add('active');
                
                // Retrigger SVG draw animations
                const svgLine = activePane.querySelector('.chart-line');
                if (svgLine) {
                    svgLine.style.animation = 'none';
                    svgLine.offsetHeight; // Trigger reflow
                    svgLine.style.animation = 'draw-chart 2.2s cubic-bezier(0.25, 1, 0.5, 1) forwards';
                }
            }
        });
    });

    // 6. Custom intercepting of Django alerts and replace them with Toasts
    const djangoAlerts = document.querySelectorAll('.alert');
    djangoAlerts.forEach(alert => {
        const text = alert.textContent.replace('×', '').trim();
        let type = 'info';
        if (alert.classList.contains('alert-success')) type = 'success';
        if (alert.classList.contains('alert-warning') || alert.classList.contains('alert-error')) type = 'danger';
        
        // Trigger Toast instead of default view if it's not a block-level global maintenance alert
        if (!alert.closest('.messages-container') || text.includes('lectura')) return;
        
        // Hide standard Django alerts and trigger Toasts
        alert.style.display = 'none';
        window.showFLEXSToast(
            type === 'success' ? 'Éxito' : (type === 'danger' ? 'Alerta' : 'Notificación'),
            text,
            type,
            6000
        );
    });
});

/**
 * CSRF token helper for AJAX requests
 */
function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

const csrftoken = getCookie('csrftoken');

/**
 * Fetch wrapper with CSRF token
 */
async function fetchWithCSRF(url, options = {}) {
    const defaultOptions = {
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrftoken,
        },
    };

    const mergedOptions = {
        ...defaultOptions,
        ...options,
        headers: {
            ...defaultOptions.headers,
            ...options.headers,
        },
    };

    return fetch(url, mergedOptions);
}
