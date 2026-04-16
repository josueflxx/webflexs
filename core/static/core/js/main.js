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
        const adminDragScrollSelector = [
            '.toolbar-actions',
            '.form-actions',
            '.cc-quick-actions',
            '.cc-links',
            '.cc-tabs',
            '.products-table-wrapper',
            '.category-table-wrap',
            '.execution-table-wrap',
            '.admin-table-container',
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
